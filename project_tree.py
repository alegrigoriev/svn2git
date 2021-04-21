#   Copyright 2021-2023 Alexandre Grigoriev
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

from __future__ import annotations
from typing import Iterator

import io
import os
import re
from pathlib import Path
import shutil
import json
from types import SimpleNamespace
import git_repo
import hashlib
from exceptions import Exception_history_parse, Exception_cfg_parse
import concurrent.futures

from history_reader import *
from lookup_tree import *
from mergeinfo import *
from dependency_node import *
import project_config
import format_files

TOTAL_FILES_REFORMATTED = 0
TOTAL_BYTES_IN_FILES_REFORMATTED = 0

# The function returns True if there are some mapped items, or no unmapped items
def get_directory_mapped_status(tree, unmapped_dir_list, prefix='/'):
	unmapped_subdirs = []
	has_mapped_subdirs = False
	has_items = tree.get_used_by('') is not None
	for path, subtree in tree.dict.items():
		# Only consider subdirectories which are not terminal leaves with a branch attached
		if subtree.mapped is True:
			has_mapped_subdirs = True
		elif get_directory_mapped_status(subtree, unmapped_subdirs, prefix + path + '/'):
			has_mapped_subdirs = True
		elif tree.get_used_by('') is not None:
			unmapped_dir_list.append(prefix + path)

	if has_mapped_subdirs:
		unmapped_dir_list += unmapped_subdirs

	return has_mapped_subdirs

def find_tree_prefix(old_git_tree, new_tree, git_repo):
	# Get filenames of git tree
	old_tree_names = git_repo.ls_tree(old_git_tree, '-r', '--full-tree', '--name-only')
	new_tree_names = [pathname for (pathname, obj) in new_tree]

	# Now reverse the names and sort the combined list
	# Names of the old tree will have the leading slash. They will sort after the similar names of the new tree,
	# after path component reversal
	reversed_old_list = ['/'.join((lambda l : (l.reverse(), l)[1])(name.split('/'))) + '/' for name in old_tree_names]
	reversed_new_list = ['/'.join((lambda l : (l.reverse(), l)[1])(name.split('/'))) for name in new_tree_names]

	combined_list = reversed_old_list + reversed_new_list
	combined_list.sort()

	ii = iter(combined_list)
	prefixes = {}
	prev_str = next(ii, None)
	curr_str = next(ii, None)

	while prev_str and curr_str:
		if prev_str.endswith('/') or not curr_str.endswith('/'):
			prev_str = curr_str
			curr_str = next(ii, None)
			continue
		# We have two consecutive lines, previous without a trailing slash (name from new list),
		# and current with a trailing slash (name from old list)
		if curr_str.startswith(prev_str + '/'):
			prefix = curr_str[len(prev_str):-1]
			if prefix in prefixes:
				prefixes[prefix] += 1
			else:
				prefixes[prefix] = 1
		prev_str = next(ii, None)
		curr_str = next(ii, None)

	# find out which prefix had the most occurrence
	prefixes = list(prefixes.items())
	if not prefixes:
		return ''

	prefixes.sort(key=lambda t : t[1], reverse=True)
	prefix = prefixes[0][0].split('/')
	# the prefix is currently reversed, with slashes and both sides
	prefix.reverse()
	return '/'.join(prefix)

def path_in_dirs(dirs, path):
	for directory in dirs:
		if path.startswith(directory):
			return True
	return False
# branch_changed set to True if there is a meaningful change in the tree (outside of merged directories)

class author_props:
	def __init__(self, author, email):
		self.author = author
		self.email = email
		return

	def __str__(self):
		return "%s <%s>" % (self.author, self.email)

def log_to_paragraphs(log):
	# Split log message to paragraphs
	paragraphs = []
	log = log.replace('\r\n', '\n')
	if log.startswith('\n\n'):
		paragraphs.append('')

	log = log.strip('\n \t')
	for paragraph in log.split('\n\n'):
		paragraph = paragraph.rstrip(' \t').lstrip('\n')
		if paragraph:
			paragraphs.append(paragraph)
	return paragraphs

class revision_props:
	def __init__(self, revision, log, author_info, date):
		self.revision = revision
		self.log = log
		self.author_info = author_info
		self.date = date
		return

### project_branch_rev keeps result for a processed revision
class project_branch_rev(async_workitem):
	def __init__(self, branch:project_branch, prev_rev=None):
		super().__init__(executor=branch.executor, futures_executor=branch.proj_tree.futures_executor)
		self.rev = None
		self.branch = branch
		self.log_file = branch.proj_tree.log_file
		self.commit = None
		self.rev_commit = None
		self.staged_git_tree = None
		self.committed_git_tree = None
		self.committed_tree = None
		self.staged_tree:git_tree = None
		# Next commit in history
		self.next_rev = None
		self.prev_rev = prev_rev
		# revisions_to_merge is a map of revisions pending to merge, keyed by (branch, index_seq).
		self.revisions_to_merge = None
		self.files_staged = 0
		self.staging_info = None
		# Even if the new tree is different from old tree, if those
		# changes are only on the subdirectory branches, this revision
		# doesn't need a commit.
		# Also, if those changes are only on the merged child branches,
		# this revision doesn't need an immediate commit.
		# In those cases, changes_present will be false.
		self.changes_present = False
		self.need_commit = False
		self.skip_commit = None
		# any_changes_present is set to true if stagelist was not empty
		self.any_changes_present = False
		self.staging_base_rev = None
		if prev_rev is None:
			self.tree:git_tree = None
			self.merged_revisions = {}
			# self.mergeinfo keeps the combined effective svn:mergeinfo attribute of the revision
			self.mergeinfo = mergeinfo()
			# self.tree_mergeinfo keeps the svn:mergeinfo attributes of the directory items of the revision
			self.tree_mergeinfo = tree_mergeinfo()
		else:
			prev_rev.next_rev = self
			self.tree:git_tree = prev_rev.tree
			# merged_revisions is a map of merged revisions keyed by (branch, index_seq).
			# It either refers to the previous revision's map,
			# or a copy is made and modified
			# Its values are tuples (merged_revision, revision_merged_at)
			self.merged_revisions = prev_rev.merged_revisions
			# propagate previous mergeinfo and tree_mergeinfo
			self.mergeinfo = prev_rev.mergeinfo
			# tree_mergeinfo will be copied on modification.
			self.tree_mergeinfo = prev_rev.tree_mergeinfo

		self.index_seq = branch.index_seq
		# list of rev-info the commit on this revision would depend on - these are parent revs for the rev's commit
		self.parents = []
		self.merge_from_dict = None
		self.copy_sources = None
		self.cherry_pick_revs = None
		self.merge_children = []
		self.props_list = []
		self.change_id = None
		return

	def set_revision(self, revision):
		self.tree = revision.tree.find_path(self.branch.path)
		if self.tree is None:
			return None

		self.rev = revision.rev
		for skip_commit in self.branch.skip_commit_list:
			if skip_commit.revs and rev_in_ranges(skip_commit.revs, self.rev):
				self.skip_commit = skip_commit
				break

		self.add_revision_props(revision)

		return self

	def get_merged_from_str(self):
		if self.merge_from_dict is None:
			return ""

		merge_msg = []

		for (branch, ranges), paths in self.merge_from_dict.items():
			revs_str = ranges_to_str(ranges)
			# The paths are already normalized
			if branch is None:
				# Absolute paths here
				merge_msg.append("Merged-path(s): %s; revs:%s" % (','.join(paths), revs_str))
				continue

			if paths and paths[-1]:
				merge_msg.append("Merged-from: %s; revs:%s; path(s):%s" % (branch.path, revs_str, ','.join(paths)))
			else:
				merge_msg.append("Merged-from: %s, revs:%s" % (branch.path, revs_str))

		if not self.cherry_pick_revs:
			return '\n'.join(merge_msg)

		# Sort by ascending revision number
		self.cherry_pick_revs.sort(key=lambda rev_info : rev_info.rev)
		# Commit list without duplicates
		cherry_pick_commits = {}
		for rev_info in self.cherry_pick_revs:
			if rev_info.rev_commit is None:
				continue

			# Need to check here for the revision merged because at process_merge_delta time
			# they're not in the merged rev dictionary yet
			if self.is_merged_from(rev_info):
				continue

			change_id = rev_info.change_id
			if rev_info.commit not in cherry_pick_commits:
				cherry_pick_commits[rev_info.commit] = rev_info

		if len(cherry_pick_commits) == 1:
			# Make the new commit inherit Change-Id
			self.change_id = change_id

		for rev_info in cherry_pick_commits.values():
			refname = re.sub('(?:^refs/(?:heads/)?)(.*)?', r'\1', rev_info.branch.refname)
			if not refname:
				refname = rev_info.branch.path

			merge_msg.append("Cherry-picked-from: %s %s;%d" % (rev_info.commit, refname, rev_info.rev))
			if rev_info.change_id != self.change_id:
				merge_msg[-1] += " Change-Id: %s" % (rev_info.change_id)

		return '\n'.join(merge_msg)

	### The function returns a single revision_props object, with:
	# .log assigned a list of text paragraphs,
	# .author, date, email, revision assigned from first revision_props
	def get_combined_revision_props(self, base_rev=None, empty_message_ok=False, decorate_revision_id=False):
		props_list = self.props_list
		if not props_list:
			return None

		prop0 = props_list[0]
		msg = prop0.log.copy()

		msg_tail_len = 0
		for prop in props_list[1:]:
			if self.branch.child_merge_dirs:
				log = "Author: %s, on %s" % (prop.author_info, prop.date)
				if decorate_revision_id:
					log = "SVN-revision: %s, %s" % (prop.revision.rev, log)

				if msg_tail_len < 2 or len(props_list) < 6:
					log += '\n' + '\n\n'.join(prop.log)
				msg.append(log)
				msg_tail_len += 1
				continue

			# These messages are combined because of SkipCommit specification
			# Drop repeating and empty paragraphs
			for paragraph in prop.log:
				if not paragraph and msg:
					# drop empty paragraphs
					continue
				for prev_paragraph in msg:
					if prev_paragraph.startswith(paragraph):
						break
				else:
					# No similar paragraph already, can append
					msg.append(paragraph)

			continue

		if not (msg or empty_message_ok):
			msg = self.make_change_description(base_rev)
		elif msg and not msg[0]:
			msg[0] = self.make_change_description(base_rev)[0]

		if not (msg or empty_message_ok) or decorate_revision_id:
			msg.append("SVN-revision: %s" % prop0.revision.rev)

		return revision_props(prop0.revision, msg, prop0.author_info, prop0.date)

	def get_commit_revision_props(self, base_rev):
		decorate_revision_id=getattr(self.branch.proj_tree.options, 'decorate_revision_id', False)
		props = self.get_combined_revision_props(base_rev, decorate_revision_id=decorate_revision_id)

		merge_msg = self.get_merged_from_str()

		if getattr(self.branch.proj_tree.options, 'decorate_change_id', False):
			# get_merged_from_str() may find out a change id to inherit from cherry-picked commit
			if not self.change_id:
				h = hashlib.sha1()
				h.update(self.tree.get_hash())
				h.update(bytes('COMMIT\n%s %s\n%s'
					% (str(props.author_info), props.date, "\n\n".join(props.log)), encoding='utf-8'))
				self.change_id = h.hexdigest()

			props.log.append('Change-Id: I' + self.change_id)

		if merge_msg:
			props.log.append(merge_msg)

		return props

	### The function sets or adds the revision properties for the upcoming commit
	def add_revision_props(self, revision):
		props_list = self.props_list
		if props_list and props_list[0].revision is revision:
			# already there
			return

		log = revision.log
		if revision.author:
			author_info = self.branch.proj_tree.map_author(revision.author)
		else:
			# git commit-tree barfs if author is not provided
			author_info = author_props("(None)", "none@localhost")

		date = str(revision.datetime)

		for edit_msg in self.branch.edit_msg_list:
			if edit_msg.revs and not rev_in_ranges(edit_msg.revs, self.rev):
				continue
			log, count = edit_msg.match.subn(edit_msg.replace, log, edit_msg.max_sub)
			if count and edit_msg.final:
				break
			continue

		props_list.insert(0,
				revision_props(revision, log_to_paragraphs(log), author_info, date))
		return

	def make_change_description(self, base_rev):
		# Don't make a description if the base revision is an imported commit from and appended repo
		if base_rev is None:
			base_tree = None
			base_branch = None
		elif base_rev.tree is not None or base_rev.commit is None:
			base_tree = base_rev.committed_tree
			base_branch = base_rev.branch
		else:
			return []

		added_files = []
		changed_files = []
		deleted_files = []
		added_dirs = []
		deleted_dirs = []
		# staged_tree could be None. Invoke the comparison in reverse order,
		# and swap the result
		for t in self.tree.compare(base_tree):
			path = t[0]
			obj2 = t[1]
			obj1 = t[2]

			if path_in_dirs(self.branch.ignore_dirs, path):
				continue
			if self.branch.ignore_file(path):
				continue

			if obj1 is not None and obj1.is_hidden():
				obj1 = None
			if obj2 is not None and obj2.is_hidden():
				obj2 = None
			if obj1 is None and obj2 is None:
				continue

			if obj1 is None:
				# added items
				if obj2.is_dir():
					added_dirs.append((path, obj2))
				else:
					added_files.append((path, obj2))
				continue
			if obj2 is None:
				# deleted items
				if base_branch is None: pass
				elif path_in_dirs(base_branch.ignore_dirs, path):
					continue
				if base_branch.ignore_file(path):
					continue

				if obj1.is_dir():
					deleted_dirs.append((path, obj1))
				else:
					deleted_files.append((path, obj1))
				continue
			
			if obj1.is_file():
				changed_files.append(path)
			continue

		# Find renamed directories
		renamed_dirs = []
		for new_path, tree2 in added_dirs:
			# Find similar tree in deleted_dirs
			for t in deleted_dirs:
				old_path, tree1 = t
				metrics = tree2.get_difference_metrics(tree1)
				if metrics.added + metrics.deleted < metrics.identical + metrics.different:
					renamed_dirs.append((old_path, new_path))
					deleted_dirs.remove(t)
					for t in deleted_files.copy():
						if t[0].startswith(old_path):
							deleted_files.remove(t)
					for t in added_files.copy():
						if t[0].startswith(new_path):
							added_files.remove(t)
					break
				continue
			continue

		# Find renamed files
		renamed_files = []
		for t2 in added_files.copy():
			# Find similar tree in deleted_dirs
			new_path, file2 = t2
			for t1 in deleted_files:
				old_path, file1 = t1
				# Not considering renames of empty files
				if file1.data and file1.data_sha1 == file2.data_sha1:
					renamed_files.append((old_path, new_path))
					added_files.remove(t2)
					deleted_files.remove(t1)
					break
				continue
			continue

		title = ''
		long_title = ''
		if added_files:
			if title:
				title += ', added files'
				long_title += ', added ' + ', '.join((path for path, file1 in added_files))
			else:
				title = 'Added files'
				long_title += 'Added ' + ', '.join((path for path, file1 in added_files))

		if deleted_files:
			if title:
				title += ', deleted files'
				long_title += ', deleted ' + ', '.join((path for path, file1 in deleted_files))
			else:
				title = 'Deleted files'
				long_title += 'Deleted ' + ', '.join((path for path, file1 in deleted_files))

		if changed_files:
			if title:
				title += ', changed files'
				long_title += ', changed ' + ', '.join(changed_files)
			else:
				title = 'Changed files'
				long_title += 'Changed ' + ', '.join(changed_files)

		if renamed_files or renamed_dirs:
			if title:
				long_title += ', renamed ' + ', '.join(("%s to %s" % (old_path, new_path) for old_path, new_path in (*renamed_dirs,*renamed_files)))
			else:
				long_title += 'Renamed ' + ', '.join(("%s to %s" % (old_path, new_path) for old_path, new_path in (*renamed_dirs,*renamed_files)))

		if len(long_title) < 100:
			return [long_title]

		if renamed_files:
			if title:
				title += ', renamed files'
			else:
				title = 'Renamed files'

		if renamed_dirs:
			if title:
				title += ', renamed directories'
			else:
				title = 'Renamed directories'

		log = []
		for path, file1 in added_files:
			log.append("Added file: %s" % (path))

		for path, file1 in deleted_files:
			log.append("Deleted file: %s" % (path))

		for path in changed_files:
			log.append("Changed file: %s" % (path))

		for old_path, new_path in renamed_files:
			log.append("Renamed file: %s to: %s" % (old_path, new_path))

		for old_path, new_path in renamed_dirs:
			log.append("Renamed directory: %s to: %s" % (old_path, new_path))

		if len(log) <= 1:
			return log

		return [title, '\n'.join(log)]

	### process_svn_mergeinfo processes svn:mergeinfo of changelist items.
	def process_svn_mergeinfo(self, path, obj1, obj2):
		# path here is relative to the branch root
		# extract new revisions merged
		if obj1 is not None:
			obj1_mergeinfo = obj1.svn_mergeinfo
		else:
			obj1_mergeinfo = ""

		if obj2 is not None:
			obj2_mergeinfo = obj2.svn_mergeinfo
		else:
			obj2_mergeinfo = ""

		if obj1_mergeinfo == obj2_mergeinfo:
			# No changes
			return

		if self.branch.proj_tree.log_merges_verbose:
			print('SVN:MERGEINFO %s;%d:\n    PREV: "%s"' % (self.branch.path + path, self.rev,
												   '\n          '.join(obj1_mergeinfo.splitlines())), file=self.log_file)
			print('     NEW: "%s"' % ('\n          '.join(obj2_mergeinfo.splitlines())), file=self.log_file)

		if self.tree_mergeinfo is self.prev_rev.tree_mergeinfo:
			self.tree_mergeinfo = self.tree_mergeinfo.copy()

		self.tree_mergeinfo.set_mergeinfo_str(path, obj2_mergeinfo)

		return

	def can_recreate_merge(self, added_ranges, rev_to_merge, prev_mergeinfo):
		branch = self.branch
		proj_tree = branch.proj_tree
		# The whole source branch path marked as merged. Make a merge commit for it.
		# See if we're merging full unmerged range
		# 1. Find out which revision 'merged_branch' or its ancestor branched off the current branch
		unmerged_ranges = self.find_unmerged_ranges(rev_to_merge, traverse_ancestor_branches=True)

		# see if self.mergeinfo covers all revisions from unmerged_ranges
		for (first_rev_to_merge, last_rev_to_merge) in unmerged_ranges:

			already_merged_ranges = prev_mergeinfo.get(last_rev_to_merge.branch.path)
			if already_merged_ranges and already_merged_ranges[-1][1] >= last_rev_to_merge.rev:
				# There's already a merged revision after the revision we need to merge now
				# Don't try to make a branch for it
				return False

			# first_rev_to_merge is not part of the range
			last_merged_at_revision = last_rev_to_merge.get_merged_at_revision(self,traverse_ancestor_branches=True)
			while last_rev_to_merge is not None:
				if first_rev_to_merge is not None \
					and last_rev_to_merge.rev <= first_rev_to_merge.rev:
					break

				if last_merged_at_revision is last_rev_to_merge:
					break

				next_last_rev_to_merge = last_rev_to_merge.prev_rev.walk_back_empty_revs()
				rev = last_rev_to_merge.rev
				if not rev_in_ranges(added_ranges, rev):
					# Check if we can ignore this unmerged revision because of IgnoreUnmerged attribute
					for t in last_rev_to_merge.tree.compare(next_last_rev_to_merge.tree if next_last_rev_to_merge is not None else None):
						obj1 = t[1]
						obj2 = t[2]
						if obj1 is not None:
							if obj1.is_dir():
								continue
						elif obj2.is_dir():
							continue

						if not branch.ignore_unmerged.fullmatch(t[0]):
							if not proj_tree.log_merges:
								return False

							print("UNMERGED path %s doesn't match any IgnoreUnmerged specicifation"
								% (t[0]), file=self.log_file)
							last_rev_to_merge = None
						continue

					if last_rev_to_merge is None:
						print('UNMERGED branch %s;r%d: revision %d not in mergeinfo'
							% (rev_to_merge.branch.path, rev_to_merge.rev, rev), file=self.log_file)
						return False

				if next_last_rev_to_merge.rev is None:
					# The range reached the beginning of the branch
					break
				if next_last_rev_to_merge is last_rev_to_merge:
					break
				last_rev_to_merge = next_last_rev_to_merge
				continue
			continue
		return True

	### process_merge_delta processes mergeinfo difference from the previous revision
	# When new merged revisions appear for a file, the commit parent will be added, and
	# "Merged-from:" line will be added to the commit message
	def process_merge_delta(self):
		branch = self.branch
		proj_tree = branch.proj_tree
		prev_rev = self.prev_rev

		prev_tree_mergeinfo = prev_rev.tree_mergeinfo
		# See if we need to find the root mergeinfo. Mergeinfo can be inherited from a parent directory.
		if branch.inherit_mergeinfo:
			parent_mergeinfo = self.tree_mergeinfo.get("")
			if not parent_mergeinfo:
				parent_mergeinfo, found_path = branch.find_mergeinfo(find_inherited=True)

				prev_parent_mergeinfo = prev_tree_mergeinfo.get("..")
				if (prev_parent_mergeinfo or parent_mergeinfo) \
					and parent_mergeinfo != prev_parent_mergeinfo:
					if self.tree_mergeinfo is prev_tree_mergeinfo:
						self.tree_mergeinfo = prev_tree_mergeinfo.copy()
					self.tree_mergeinfo.set_mergeinfo('..', parent_mergeinfo)

		# Process all copy sources. Mergeinfo they bring needs to be added to the previous mergeinfo
		if self.copy_sources is not None:
			for dest_path, rev_dict in self.copy_sources.items():
				# dest_path for a directory here ends with '/'
				new_path_prefix = dest_path.removeprefix(branch.path)
				for source_path, rev in rev_dict.items():
					# source_path for a directory here ends with '/'
					# The copy source may be outside of any mapped branch, but should be present in the root tree
					# The copy source revision and path are previously verified to be valid and
					# map to a present path
					prev_path_prefix = source_path
					source_rev = proj_tree.find_branch_rev(source_path, rev)
					if source_rev is None:
						source_obj = proj_tree.get_revision(rev).tree
						source_tree_mergeinfo = tree_mergeinfo()
						source_tree_mergeinfo.load_tree(source_obj, source_path, recurse_tree=True)
						prev_path_prefix = ''
					elif source_rev.branch.path == source_path:
						# The copy source is the whole branch
						prev_path_prefix = ''
						source_tree_mergeinfo = source_rev.tree_mergeinfo
					else:
						# The copy source is a subdirectory or a file in branch
						# This mergeinfo has full paths as keys
						prev_path_prefix = source_path.removeprefix(source_rev.branch.path)
						source_tree_mergeinfo = tree_mergeinfo()
						source_tree_mergeinfo.get_subtree_mergeinfo(source_rev.tree_mergeinfo, prev_path_prefix)
						prev_path_prefix = ''

					if not source_tree_mergeinfo:
						# empty
						continue
					if source_tree_mergeinfo is prev_rev.tree_mergeinfo:
						continue

					if prev_tree_mergeinfo is prev_rev.tree_mergeinfo:
						prev_tree_mergeinfo = prev_tree_mergeinfo.copy()
					# prev_path_prefix must be the source branch path to be removed from source_tree_mergeinfo
					if prev_tree_mergeinfo.add_tree_mergeinfo(source_tree_mergeinfo, prev_path_prefix, new_path_prefix) \
						and proj_tree.log_merges_verbose:
							print(" SUB mergeinfo:prev_prefix=%s,new_prefix=%s\n%s" %
								(prev_path_prefix, new_path_prefix, str(source_tree_mergeinfo)), file=self.log_file)
					continue

		# See if the mergeinfo dictionary has been changed from the previous revision
		if self.tree_mergeinfo is prev_tree_mergeinfo:
			return

		# build new mergeinfo
		self.mergeinfo = self.tree_mergeinfo.build_mergeinfo()

		if prev_tree_mergeinfo is prev_rev.tree_mergeinfo:
			prev_mergeinfo = prev_rev.mergeinfo
		else:
			prev_mergeinfo = prev_tree_mergeinfo.build_mergeinfo()

		# Newly added merged revisions may bring other merged revisions, which needs to be subtracted
		while True:
			mergeinfo_diff = self.mergeinfo.get_diff(prev_mergeinfo)
			if proj_tree.log_merges_verbose and mergeinfo_diff:
				if prev_tree_mergeinfo is not prev_rev.tree_mergeinfo:
					if prev_mergeinfo:
						print("BUILD NEW prev_mergeinfo for %s:\n%s" % (branch.path, str(prev_mergeinfo)), file=self.log_file)
				elif prev_mergeinfo:
					print("PREV mergeinfo for %s;%s:\n%s" % (prev_rev.branch.path, prev_rev.rev, str(prev_mergeinfo)), file=self.log_file)
				if self.mergeinfo:
					print("NEW mergeinfo for %s;%d:\n%s" % (branch.path, self.rev, str(self.mergeinfo)), file=self.log_file)
				print("MERGEINFO DIFF:\n\t%s" % mergeinfo_diff.__str__('\t'), file=self.log_file)

			for path, added_ranges in mergeinfo_diff.items():
				merged_branch = proj_tree.find_branch(path)
				if not merged_branch:
					new_tree_mergeinfo = proj_tree.find_tree_mergeinfo(path,
												added_ranges[-1][1],inherit=True, recurse_tree=True)
					if prev_tree_mergeinfo.add_tree_mergeinfo(new_tree_mergeinfo):
						mergeinfo_diff = None
					continue
				path = path.lstrip('/')
				rev_to_merge = merged_branch.get_revision(added_ranges[-1][1])

				if rev_to_merge is None:
					continue
				if self.is_merged_from(rev_to_merge, skip_empty_revs=True):
					continue
				# Add its mergeinfo to prev_mergeinfo
				if prev_tree_mergeinfo is prev_rev.tree_mergeinfo:
					prev_tree_mergeinfo = prev_tree_mergeinfo.copy()
				#original_tree_mergeinfo = prev_tree_mergeinfo.copy()
				if prev_tree_mergeinfo.add_tree_mergeinfo(rev_to_merge.tree_mergeinfo):
					mergeinfo_diff = None
					if proj_tree.log_merges_verbose:
						print("\nSUB MERGEINFO from %s;%d:\n%s"
							%(merged_branch.path, added_ranges[-1][1],rev_to_merge.tree_mergeinfo), file=self.log_file)
				continue
			if mergeinfo_diff is not None:
				break
			# Give it another spin
			prev_mergeinfo = prev_tree_mergeinfo.build_mergeinfo()
			continue

		self.merge_from_dict = {}

		mergeinfo_diff.normalize()
		for path, added_ranges in mergeinfo_diff.items():

			merged_branch = proj_tree.find_branch(path)
			if merged_branch is None:
				rev_to_merge = proj_tree.get_revision(added_ranges[-1][1])
				obj = rev_to_merge.tree.find_path(path)
				make_cherry_pick_revs = False

				if proj_tree.log_merges:
					print('SVN:MERGEINFO: ADDED REVS for unmapped path %s: %s'
							% (path, ranges_to_str(added_ranges)), file=self.log_file)
			else:
				make_cherry_pick_revs = True
				path = path.lstrip('/')
				rev_to_merge = merged_branch.get_revision(added_ranges[-1][1])
				if path == merged_branch.path.removesuffix('/'):
					path = ''
					recreate_merge = branch.recreate_merges.branch_merge
				else:
					path = path.removeprefix(merged_branch.path)
					# If the source tree is similar, the branches are related
					recreate_merge = branch.recreate_merges.file_merge \
							and branch.tree_is_similar(rev_to_merge)

				if rev_to_merge is None:
					if proj_tree.log_merges:
						print('SVN:MERGEINFO: ADDED REVS for branch %s path %s: %s (revision %s not present)'
								% (merged_branch.path, path,
									ranges_to_str(added_ranges), added_ranges[-1][1]), file=self.log_file)
					continue

				if self.is_merged_from(rev_to_merge, skip_empty_revs=True):
					if proj_tree.log_merges:
						print('SVN:MERGEINFO: ADDED REVS for branch %s path %s: %s (revision %s already merged)'
								% (merged_branch.path, path,
									ranges_to_str(added_ranges), added_ranges[-1][1]), file=self.log_file)
					continue

				if proj_tree.log_merges:
					print('SVN:MERGEINFO: ADDED REVS for branch %s path %s: %s'
							% (merged_branch.path, path,
								ranges_to_str(added_ranges)), file=self.log_file)

				obj = rev_to_merge.tree.find_path(path)

				if recreate_merge and self.can_recreate_merge(added_ranges, rev_to_merge, prev_mergeinfo):
					make_cherry_pick_revs = False
					self.add_branch_to_merge(merged_branch, rev_to_merge)
					print('MERGE branch %s;r%d' % (merged_branch.path, rev_to_merge.rev), file=self.log_file)
				elif obj is not None:
					if obj.is_dir():
						path += '/'
				else:
					# merged path is malformed
					continue

			# Filter revisions by the path
			filtered_ranges = []
			cherry_pick_revs = []
			while added_ranges:

				# walk the revisions back
				start, end = added_ranges.pop(-1)
				while end >= start:
					rev = rev_to_merge.rev
					if rev is None:
						break
					if type(rev_to_merge) is project_branch_rev \
						and self.is_merged_from(rev_to_merge, skip_empty_revs=True):
						break
					# object for 'end' is 'obj'
					prev_rev = rev_to_merge.prev_rev
					if prev_rev is None or prev_rev.tree is None:
						break

					prev_obj = prev_rev.tree.find_path(path)

					if rev <= end:
						if prev_obj is not obj:
							filtered_ranges.append( (rev, rev) )
							if make_cherry_pick_revs:
								cherry_pick_revs.append(rev_to_merge)
								# We need that revision commit ID for our merge message
								self.add_dependency(rev_to_merge)
								rev_to_merge.mark_need_commit()

						end = prev_rev.rev
						if end is None:
							break

					rev_to_merge = prev_rev
					obj = prev_obj

			if not filtered_ranges:
				continue
			# This will sort and combine revisions into ranges
			added_ranges = combine_ranges(filtered_ranges, [])

			if not cherry_pick_revs:
				pass
			elif self.cherry_pick_revs:
				self.cherry_pick_revs += cherry_pick_revs
			else:
				self.cherry_pick_revs = cherry_pick_revs

			# path_list is the value by the key in merge_from_dict
			# When the value is first inserted by setdefault(), an empty list is inserted as value
			# added_ranges list needs to be converted to a tuple to be hashable.
			paths_list = self.merge_from_dict.setdefault((merged_branch, tuple(added_ranges)), [])
			if path:
				# Here we're modifying path_list value in place
				paths_list.append(path)
			continue

		return

	def add_parent_revision(self, add_rev):
		if add_rev.tree is None:
			return

		if self.is_merged_from(add_rev):
			return

		key = (add_rev.branch, add_rev.index_seq)
		if self.revisions_to_merge is None:
			self.revisions_to_merge = {}
		else:
			# Check if this revision or its descendant has been added for merge already
			merged_rev = self.revisions_to_merge.get(key)
			if merged_rev is not None and merged_rev.rev >= add_rev.rev:
				return

		self.revisions_to_merge[key] = add_rev

		# Merges from merged child branches are not inherited
		if add_rev.branch.merge_parent is self.branch:
			return

		# Now add previously merged revisions from add_rev to the merged_revisions dictionary
		for (rev_info, merged_on_rev) in add_rev.merged_revisions.values():
			# Merged child branches are not inherited
			if rev_info.branch.merge_parent is add_rev.branch:
				continue

			if not self.is_merged_from(rev_info):
				self.set_merged_revision(rev_info, merged_on_rev)
			continue
		return

	def process_parent_revisions(self, HEAD):
		# Either tree is known, or previous commit was imported from previous refs
		if HEAD.tree or HEAD.commit:
			self.parents.append(HEAD)
			self.add_dependency(HEAD)

		self.process_merge_delta()	# Can add more merged revisions

		if self.branch.orphan_parent is not None:
			branch = self.branch
			if not self.parents \
				and self.revisions_to_merge is None \
				and branch.orphan_parent.tree:
				# orphan parent
				# Check if this parent is sufficiently similar to the current tree
				if self.tree_is_similar(branch.orphan_parent):
					print("LINK ORPHAN: Found parent %s;r%d for an orphan revision %s;r%d" %
						(branch.orphan_parent.branch.path, branch.orphan_parent.rev, branch.path, self.rev),
						file=self.log_file)
					self.add_parent_revision(branch.orphan_parent)
			branch.orphan_parent = None

		# Process revisions to merge dictionary, if present
		if self.revisions_to_merge is not None:
			for parent_rev in self.revisions_to_merge.values():
				# Add newly merged revisions to self.merged_revisions dict
				if self.is_merged_from(parent_rev):
					continue

				self.set_merged_revision(parent_rev)

				if parent_rev.tree is self.tree and not self.parents:
					self.changes_present = False
					self.any_changes_present = False
				self.add_dependency(parent_rev)
				if parent_rev.branch.merge_parent is self.branch and parent_rev.branch.lazy_merge_to_parent:
					self.merge_children.append(parent_rev)
				else:
					parent_rev.mark_need_commit()
					self.parents.append(parent_rev)
				continue

			# Keep self.revisions_to_merge for detecting unchanged blobs for keyword expansion

		# This commit needs to be forced out if:
		# 1) the initial tree was empty and the branch has merge children (even if it was a copy?)
		# 2) the initial tree was empty, its prev_rev has merge children, prev_rev needs to be forced to commit

		if self.changes_present or (HEAD.staged_tree is None and self.merge_children):
			self.mark_need_commit()
			if HEAD.merge_children:
				HEAD.mark_need_commit()
		elif HEAD.staged_tree is None and HEAD.merge_children:
			HEAD.mark_need_commit()

		return

	### Get which revision of the branch of interest have been merged
	def get_merged_revision(self, rev_info_or_branch, index_seq=None):
		if index_seq is None:
			index_seq = rev_info_or_branch.index_seq

		if type(rev_info_or_branch) is project_branch_rev:
			rev_info_or_branch = rev_info_or_branch.branch

		(merged_rev, merged_at_rev) = self.merged_revisions.get((rev_info_or_branch, index_seq), (None,None))
		return merged_rev

	def set_merged_revision(self, merged_rev, merged_at_rev=None):
		if merged_at_rev is None:
			merged_at_rev = self

		if self.merged_revisions is self.prev_rev.merged_revisions:
			self.merged_revisions = self.prev_rev.merged_revisions.copy()
		self.merged_revisions[(merged_rev.branch, merged_rev.index_seq)] = (merged_rev, merged_at_rev)
		return

	### Get at which revision of the branch or revision of interest have been merged
	# The revision of interest might have gotten merged into one of ancestor branches.
	# If traverse_ancestor_branches is True, find to which revision of the current branch
	# they got ultimately merged.
	def get_merged_at_revision(self, rev_info_or_branch, index_seq=None, traverse_ancestor_branches=False,recurse_ancestor_branches=False):
		if index_seq is None:
			index_seq = rev_info_or_branch.index_seq

		if type(rev_info_or_branch) is project_branch:
			rev_info_or_branch = rev_info_or_branch.HEAD

		while True:
			(merged_rev, merged_at_rev) = self.merged_revisions.get((rev_info_or_branch.branch, index_seq), (None,None))
			if merged_at_rev is None:
				if recurse_ancestor_branches:
					for (merged_rev, merged_at_rev2) in rev_info_or_branch.merged_revisions.values():
						merged_at_rev2 = self.get_merged_at_revision(merged_rev,index_seq,traverse_ancestor_branches,False)
						if merged_at_rev2 is not None \
							and (merged_at_rev is None or merged_at_rev.rev < merged_at_rev2.rev):
							merged_at_rev = merged_at_rev2
				break
			if merged_at_rev.branch is self.branch and merged_at_rev.index_seq == self.index_seq:
				break
			if not traverse_ancestor_branches:
				break
			rev_info_or_branch = merged_at_rev
			index_seq = merged_at_rev.index_seq
			continue
		return merged_at_rev

	### Find least range of revisions starting with rev_to_merge
	# not sharing common ancestors with self
	def find_unmerged_ranges(self, rev_to_merge, traverse_ancestor_branches=False):
		# Find a list of revisions in 'rev_to_merge' and its ancestors NOT merged to self.
		# These are revisions reachable from 'rev_to_merge', but not reachable from self.
		# Go over a list of ranges reachable from 'rev_to_merge': these are the revision itself and merged_revisions.
		# Find those branches in self+self.merged_revisions, these will be start of unmerged revision ranges.
		unmerged_ranges = []
		rev_to_merge = rev_to_merge.walk_back_empty_revs()
		merged_rev = self.get_merged_revision(rev_to_merge)
		if merged_rev is not None \
			and merged_rev.rev >= rev_to_merge.rev:
			return unmerged_ranges
		unmerged_ranges.append((merged_rev, rev_to_merge))

		for (rev_info, merged_at) in rev_to_merge.merged_revisions.values():
			rev_info = rev_info.walk_back_empty_revs()
			if rev_info.branch is self.branch and rev_info.index_seq == self.index_seq:
				continue

			merged_rev = self.get_merged_revision(rev_info)
			if merged_rev is not None \
				and merged_rev.rev >= rev_info.rev:
				continue

			unmerged_ranges.append((merged_rev, rev_info))
			continue
		return unmerged_ranges

	### Returns True if rev_info_or_branch (if branch, then its HEAD) is one of the ancestors of 'self'.
	# If rev_info_or_branch is a branch, its HEAD is used.
	# If skip_empty_revs is True, then the revision of interest is considered merged
	# even if it's a descendant of the merged revision, but there's been no changes
	# between them
	def is_merged_from(self, rev_info_or_branch, index_seq=None, skip_empty_revs=False):
		if type(rev_info_or_branch) is project_branch:
			branch = rev_info_or_branch
			rev_info = branch.HEAD
		else:
			branch = rev_info_or_branch.branch
			rev_info = rev_info_or_branch
		if index_seq is None:
			index_seq = rev_info.index_seq

		if branch is self.branch \
			and index_seq == self.index_seq:
			# A previous revision of the same sequence of the branch
			# is considered merged
			return True

		merged_rev = self.get_merged_revision(branch, index_seq)
		if merged_rev is None:
			return False
		if skip_empty_revs:
			rev_info = rev_info.walk_back_empty_revs()

		return merged_rev.rev >= rev_info.rev

	### walk back rev_info if it doesn't have any changes
	# WARNING: it may return a revision with rev = None
	def walk_back_empty_revs(self):
		while self.prev_rev is not None \
				and self.prev_rev.rev is not None \
				and not self.any_changes_present \
				and len(self.parents) < 2:	# not a merge commit
			self = self.prev_rev
		return self

	def add_copy_source(self, source_path, target_path, copy_rev, copy_branch=None):
		if copy_rev is None:
			return

		if copy_branch and \
			(source_path == copy_branch.path \
				or self.branch.recreate_merges.dir_copy):
			self.add_branch_to_merge(copy_branch, copy_rev)

		if self.copy_sources is None:
			self.copy_sources = {}
		copy_sources = self.copy_sources.setdefault(target_path, {})
		rev = copy_sources.setdefault(source_path, copy_rev)
		if rev < copy_rev:
			copy_sources[source_path] = copy_rev
		return

	## Adds a parent branch, which will serve as the commit's parent.
	# If multiple revisions from a branch are added as a parent, highest revision is used for a commit
	# the branch also inherits all merged sources from the parent revision
	def add_branch_to_merge(self, source_branch, rev_to_merge):
		if type(rev_to_merge) is int:
			if source_branch is None:
				return

			rev_to_merge = source_branch.get_revision(rev_to_merge)

		if rev_to_merge is None:
			return

		self.add_parent_revision(rev_to_merge)
		return

	def tree_is_similar(self, source):
		if self.tree is None:
			return False
		if type(source) is not type(self.tree):
			source = source.tree
		if source is None:
			return False

		metrics = self.tree.get_difference_metrics(source)
		return metrics.added + metrics.deleted < metrics.identical + metrics.different

	def mark_need_commit(self):
		if self.need_commit:
			#already marked
			return

		# A commit has to be forced out if:
		# 1. It has its own changes outside child branches (self.changes_present)
		# 2. Or it's used as a non-child-branch merge parent, and it has merge parents
		# 3. Or it merges child branches, and the next commit has its own changes.
		# If a commit is marked as needed, its merge parents also marked as needed
		self.need_commit = True
		for rev_info in self.parents[1:]:
			rev_info.mark_need_commit()
		for rev_info in self.merge_children:
			rev_info.mark_need_commit()
		return

	### This function is used to gather a list of merged revisions.
	# It gets called for every branch HEAD, or for every deleted HEAD
	# The branches are processed in order they are created.
	def export_merged_revisions(self, merged_revisions):
		if self.commit is None:
			# The branch HEAD is deleted or never committed
			return

		self = self.walk_back_empty_revs()
		key = (self.branch, self.index_seq)
		(rev_info, merged_on_rev) = merged_revisions.get(key, (None, None))
		if rev_info is not None:
			if rev_info.merged_revisions is self.merged_revisions:
				return

		if not self.any_changes_present:
			# This branch haven't had a meaningful change since it was created.
			# Put it down as merged
			merged_revisions[(self.branch, self.index_seq)] = (self, self)

		# Check if this HEAD is fully merged into one of its merged revisions
		for (rev_info, merged_on_rev) in self.merged_revisions.values():
			if rev_info.commit is None:
				continue

			# Do not export merged subdirectory branches
			if rev_info.branch.merge_parent is merged_on_rev.branch:
				continue
			key = (rev_info.branch, rev_info.index_seq)

			(exported_rev, exported_merged_on_rev) = merged_revisions.get(key, (None, None))
			if exported_rev is not None:
				if exported_rev.rev > rev_info.rev:
					continue
				if exported_rev.rev == rev_info.rev \
					and exported_merged_on_rev.rev >= merged_on_rev.rev:
						# it's an earlier merge
						continue

			# Advance the merged rev by same commit ID
			while rev_info.next_rev is not None \
					and rev_info.commit == rev_info.next_rev.commit:
				rev_info = rev_info.next_rev

			merged_revisions[key] = (rev_info, merged_on_rev)

		return

	### See if this revision is present in all_merged_revisions_dict
	def get_revision_merged_at(self, all_merged_revisions_dict):
		if self.tree is None:
			return None
		(merged_rev, merged_at_rev) = \
			all_merged_revisions_dict.get((self.branch, self.index_seq), (None, None))
		if merged_rev is None:
			return None

		if merged_rev is merged_at_rev:
			return merged_at_rev

		self = self.walk_back_empty_revs()
		if merged_rev.rev >= self.rev:
			return merged_at_rev
		return None

	def get_staging_base(self, HEAD):
		# Current Git tree in the index matches the SVN tree in self.HEAD
		# If there's no index, self.HEAD.tree is None
		# The base tree for staging can be either:
		# a) the current Git tree in the index. The changelist is calculated relative to HEAD.tree
		# b) If HEAD.tree is None, then the first parent will be used
		prev_rev = HEAD
		branch = self.branch

		if prev_rev.staged_tree is None and self.revisions_to_merge is not None:
			for new_prev_rev in self.revisions_to_merge.values():
				if new_prev_rev.staged_tree is None or branch.tree_prefix != new_prev_rev.branch.tree_prefix:
					continue
				# tentative parent
				# Check if this parent is sufficiently similar to the current tree
				if self.tree is new_prev_rev.staged_tree:
					break
				if self.tree_is_similar(new_prev_rev.staged_tree):
					break
				continue
			else:
				# A candidate staging base not found
				new_prev_rev = None

			if new_prev_rev:
				self.mergeinfo.add_mergeinfo(new_prev_rev.mergeinfo)
				self.tree_mergeinfo.add_tree_mergeinfo(new_prev_rev.tree_mergeinfo)
				if self.copy_sources:
					self.copy_sources.get(branch.path, {}).pop(new_prev_rev.branch.path, None)
				prev_rev = new_prev_rev

		self.staging_base_rev = prev_rev

		return prev_rev

	def get_difflist(self, old_tree, new_tree, path_prefix=""):
		branch = self.branch
		if old_tree is None:
			old_tree = branch.proj_tree.empty_tree
		if new_tree is None:
			new_tree = branch.proj_tree.empty_tree

		difflist = []
		for t in old_tree.compare(new_tree, path_prefix, expand_dir_contents=True):
			path = t[0]
			obj1 = t[1]
			obj2 = t[2]
			item1 = t[3]
			item2 = t[4]

			if path_in_dirs(branch.ignore_dirs, path):
				continue

			if branch.ignore_file(path):
				if not obj2:
					continue
				full_path = branch.path + path
				ignored_path = getattr(obj2, 'ignored_path', None)
				if ignored_path and (full_path == ignored_path or full_path.endswith('/' + ignored_path)):
					continue
				# Print the message only once for the given blob, when it's used with the same relative path
				# or with the parent's relative path
				if obj2.is_file():
					parent_dir = full_path.removesuffix(item2.name)
					if not parent_dir or not branch.ignore_file(parent_dir):
						print('IGNORED: File %s' % (full_path,), file=self.log_file)
				else:
					parent_dir = path.removesuffix(item2.name + '/')
					if not parent_dir or not branch.ignore_file(parent_dir):
						print('IGNORED: Directory %s' % (full_path,), file=self.log_file)
					# else The whole parent directory is ignored; don't print the message for every subdirectory
				obj2.ignored_path = full_path
				continue

			child_merge_dir = path_in_dirs(branch.child_merge_dirs, path)
			if not child_merge_dir:
				self.process_svn_mergeinfo(path, obj1, obj2)

			difflist.append( (path, obj1, obj2, item1, item2, child_merge_dir) )
			continue

		return difflist

	def build_difflist(self, HEAD):

		# Count total number of staged files, besides from injected files
		self.files_staged = HEAD.files_staged

		return self.get_difflist(HEAD.tree, self.tree)

	def delete_staged_file(self, stagelist, post_staged_list, path):
		branch = self.branch
		# Check if the path is one of the injected files
		injected_file = branch.inject_files.get(path)
		if injected_file:
			post_staged_list.append(SimpleNamespace(path=path, obj=injected_file,
										mode=branch.get_file_mode(path, injected_file)))

		stagelist.append(SimpleNamespace(path=path, obj=None, mode=0))

		# count staged files
		self.files_staged -= 1
		return

	def get_stagelist(self, difflist, stagelist, post_staged_list):
		branch = self.branch

		for t in difflist:
			path = t[0]
			obj1 = t[1]
			obj2 = t[2]
			item1 = t[3]
			item2 = t[4]
			child_merge_dir = t[5]

			if obj1 is not None and obj1.is_hidden():
				obj1 = None
			if obj2 is not None and obj2.is_hidden():
				obj2 = None
			if obj1 is None and obj2 is None:
				continue

			if obj2 is None:
				# a path is deleted
				if not obj1.is_file():
					# handle svn:ignore attribute
					if not obj1.svn_ignore:
						if not branch.placeholder_tree:
							continue
						if path == '':
							# No placeholder in the root directory of the branch
							continue
						# See if the directory being deleted hasn't had any files
						for (obj_path, obj) in obj1:
							if obj.is_file() and not branch.ignore_file(path + obj_path):
								# a file is present and it's not ignored
								break
						# No need to delete directories. The placeholder will be deleted because the placeholder_tree is deleted
						else:
							# delete placeholder file
							self.get_stagelist(self.get_difflist(branch.placeholder_tree, None, path),
								stagelist, post_staged_list)
						continue
					# Delete .gitignore file previously created from svn:ignore
					path += '.gitignore'

				self.delete_staged_file(stagelist, post_staged_list, path)
				if not child_merge_dir:
					self.changes_present = True
				continue

			if not obj2.is_file():
				# handle svn:ignore attributes
				if obj1 is not None:
					prev_ignore_spec = obj1.svn_ignore
				else:
					prev_ignore_spec = b''
				ignore_spec = obj2.svn_ignore

				if branch.placeholder_tree and path != '':
					# See if the directory being created or modified will not have any files
					for (obj_path, obj) in obj2:
						if obj.is_file() and not branch.ignore_file(path + obj_path):
							if obj1:
								# check if the directory was previously empty
								for (obj_path, obj) in obj1:
									if obj.is_file() and not branch.ignore_file(path + obj_path):
										break
								else:
									if not prev_ignore_spec:
										# delete placeholder file
										self.get_stagelist(self.get_difflist(branch.placeholder_tree, None, path),
											stagelist, post_staged_list)
							break
					else:
						if not ignore_spec:
							# Inject placeholder file
							self.get_stagelist(self.get_difflist(None, branch.placeholder_tree, path),
								stagelist, post_staged_list)

				if ignore_spec == prev_ignore_spec:
					continue

				path += '.gitignore'
				if not ignore_spec:
					# Delete .gitignore file
					self.delete_staged_file(stagelist, post_staged_list, path)
					if not child_merge_dir:
						self.changes_present = True
					continue

				if not prev_ignore_spec:
					# .gitignore not previously present
					obj1 = None

				obj2 = branch.proj_tree.make_blob(ignore_spec, None)

			if item2 is not None and hasattr(item2, 'mode'):
				mode = item2.mode
				prev_mode = -mode
			else:
				mode = branch.get_file_mode(path, obj2)
				if obj1:
					prev_mode = branch.get_file_mode(path, obj1)

			if obj1 is None:
				self.files_staged += 1
			elif not obj1.is_file():
				pass
			elif obj1.data_sha1 == obj2.data_sha1 and obj1.svn_keywords == obj2.svn_keywords and mode == prev_mode:
				continue
			else:
				# Check that formatting hasn't changed for the path
				format_str1 = getattr(obj1.fmt, 'format_str', None)
				format_str2 = getattr(obj2.fmt, 'format_str', None)
				if format_str1 != format_str2:
					print("WARNING: Formatting for file %s in branch %s changed" % (path, branch.path),file=self.log_file)
					print("Previous:", format_str1, file=self.log_file)
					print("     New:", format_str2, file=self.log_file)

			if obj2.data_sha1 != obj2.pretty_data_sha1 and self.revisions_to_merge is not None:
				for prev_rev in self.revisions_to_merge.values():
					if self.prev_rev is prev_rev or prev_rev.tree is None:
						# This is the base revision which obj1 was taken from
						continue
					obj = prev_rev.tree.find_path(path)
					if obj and obj.data_sha1 == obj2.data_sha1:
						obj2 = obj
						break

			if not child_merge_dir:
				# a path is created or replaced. This commit has to be forced out
				self.changes_present = True

			stagelist.append(SimpleNamespace(path=path, obj=obj2, mode=mode))
			continue

		return

	def build_stagelist(self, HEAD):
		HEAD = self.get_staging_base(HEAD)

		staging_info = async_workitem(executor=self.executor, futures_executor=self.futures_executor)

		difflist = self.build_difflist(HEAD)
		# Parent revs need to be processed before building the stagelist
		self.process_parent_revisions(HEAD)

		branch = self.branch

		stagelist = []
		post_staged_list = []
		self.get_stagelist(difflist, stagelist, post_staged_list)

		if self.files_staged == 0:
			if HEAD.files_staged:
				# delete injected files, too
				for path in branch.inject_files:
					stagelist.insert(0, SimpleNamespace(path=path, obj=None, mode=0))
		elif HEAD.files_staged == 0:
			# old tree was empty, new tree is not empty. Inject files:
			for (path, obj2) in branch.inject_files.items():
				stagelist.insert(0, SimpleNamespace(path=path, obj=obj2,
										mode=branch.get_file_mode(path, obj2)))
		else:
			stagelist += post_staged_list

		# If any .gitattributes file changes in the changelist, make an environment with a new workdir
		for item in stagelist:
			if item.path.endswith('.gitattributes'):
				# .gitattributes changed, make new environment
				branch.make_gitattributes_tree(self.tree, HEAD.tree)
				break
		else:
			if HEAD is not self.prev_rev or branch.gitattributes_sha1 is None:
				branch.make_gitattributes_tree(self.tree, self.prev_rev.tree)

		# Need to save the git environment now, after make_gitattributes_tree(),
		# which can update the environment
		self.git_env = branch.git_env

		for item in stagelist:
			obj = item.obj
			if obj is None:
				continue
			if obj.git_sha1 is not None:
				if type(obj.git_sha1) is str:
					continue
				staging_info.add_dependency(obj.git_sha1)
				continue

			if obj.is_symlink():
				path = None
				fmt = None
				data = obj.data[5:]
			else:
				path = item.path
				fmt = obj.fmt
				data = obj.pretty_data

			h = hashlib.sha1()
			h.update(obj.get_hash())
			h.update(branch.gitattributes_sha1)
			if obj.fmt is not None:
				h.update(format_files.sha1)
				h.update(obj.fmt.get_format_tag())
			h.update(item.path.encode())

			sha1 = h.hexdigest()
			git_sha1 = branch.proj_tree.sha1_map.get(sha1, None)
			if git_sha1 is not None:
				obj.git_sha1 = git_sha1
				continue

			git_sha1 = branch.proj_tree.prev_sha1_map.get(sha1, None)
			if git_sha1 is not None:
				branch.proj_tree.sha1_map[sha1] = git_sha1
				obj.git_sha1 = git_sha1
				continue

			obj.git_sha1 = async_workitem(executor=branch.executor)
			staging_info.add_dependency(obj.git_sha1)
			obj.git_sha1.set_async_func(branch.hash_object, data,
								path, sha1, fmt, self.git_env, self.log_file)
			obj.git_sha1.ready()
			continue

		self.staged_tree = self.tree
		self.any_changes_present = len(stagelist) != 0

		if HEAD is not self.prev_rev:
			# Need to read the new staging base
			read_tree_info = async_workitem(HEAD.staging_info,
									futures_executor=branch.proj_tree.write_tree_executor)
			staging_info.add_dependency(read_tree_info)
			read_tree_info.set_async_func(self.read_tree_callback)
			read_tree_info.ready()
		elif HEAD.staging_info:
			staging_info.add_dependency(HEAD.staging_info)

		if stagelist:
			staging_info.set_async_func(self.stage_changes_callback, stagelist)
			staging_info.ready()

			# Replace staging_info with write-tree callback async item
			staging_info = async_workitem(staging_info,
										futures_executor=branch.proj_tree.write_tree_executor)
			staging_info.set_async_func(self.write_tree_callback)
		else:
			staging_info.set_completion_func(self.no_stage_changes_callback)

		self.add_dependency(staging_info)
		self.staging_info = staging_info
		staging_info.ready()

		return

	def read_tree_callback(self):
		self.branch.git_repo.read_tree(self.staging_base_rev.staged_git_tree, '-i', '--reset', env=self.git_env)
		return

	def no_stage_changes_callback(self):
		self.staged_git_tree = self.staging_base_rev.staged_git_tree
		return

	def stage_changes_callback(self, stagelist):
		self.branch.stage_changes(stagelist, self.git_env)
		return

	def write_tree_callback(self):
		self.staged_git_tree = self.branch.git_repo.write_tree(self.git_env)
		return

## project_branch - keeps a context for a single change branch (or tag) of a project
class project_branch(dependency_node):

	def __init__(self, proj_tree:project_history_tree, branch_map, workdir:Path, parent_branch):
		super().__init__(executor=proj_tree.executor)
		self.path = branch_map.path
		self.proj_tree = proj_tree
		# Matching project's config
		self.cfg:project_config.project_config = branch_map.cfg
		self.git_repo = proj_tree.git_repo

		self.inherit_mergeinfo = branch_map.inherit_mergeinfo
		self.delete_if_merged = branch_map.delete_if_merged
		self.recreate_merges = branch_map.recreate_merges
		self.ignore_unmerged = branch_map.ignore_unmerged
		self.link_orphans = branch_map.link_orphans
		self.add_tree_prefix = branch_map.add_tree_prefix

		# ignore_dirs are paths of non-merging child branch dirs, with trailing slash
		# files in those directories are ignored in the change list
		self.ignore_dirs = []
		self.parent = parent_branch
		# child_merge_dirs are directories of merged child branches.
		# Changes in those directories are staged, but don't trigger a commit
		self.child_merge_dirs = []
		self.merge_parent = None
		# Not blocking commits on this branch, until it has merged children added
		self.block_commits = None

		if parent_branch:
			relative_path = branch_map.path.removeprefix(parent_branch.path)
			if branch_map.merge_to_parent:
				self.merge_parent = parent_branch
				self.lazy_merge_to_parent = branch_map.lazy_merge_to_parent
				# These directories are only ignored for the purpose of
				# deciding to force make a commit.
				parent_branch.child_merge_dirs.append(relative_path)
				if self.lazy_merge_to_parent:
					# A regular branch doesn't block its commits
					# Only if lazily merged branches are added, the HEAD will get marked as depending on this branch,
					# Until all revisions of the source dump are processed,
					# which means no more merge sources can be added
					parent_branch.block_commits = dependency_node(parent_branch)
					parent_branch.block_commits.ready()
			else:
				# If not merging to the parent branch, add ignore specifications.
				parent_branch.ignore_dirs.append(relative_path)

		self.revisions = []
		self.orphan_parent = None
		self.first_revision = None
		self.tree_prefix = ''

		self.inject_files = {}
		for file in self.cfg.inject_files:
			if file.path and file.branch.match(self.path):
				self.inject_files[file.path] = file.blob

		for file in branch_map.inject_files:
			if file.data is not None and file.blob is None:
				file.blob = proj_tree.make_blob(file.data, None)
			self.inject_files[file.path] = file.blob

		self.edit_msg_list = []
		for edit_msg in *branch_map.edit_msg_list, *self.cfg.edit_msg_list:
			if edit_msg.branch.fullmatch(self.path):
				self.edit_msg_list.append(edit_msg)
			continue

		self.ignore_files = branch_map.ignore_files
		self.format_specifications = branch_map.format_specifications
		self.skip_commit_list = branch_map.skip_commit_list + branch_map.cfg.skip_commit_list

		# If need to preserve empty directories, this gets replaced with
		# a tree which contains the placeholder file
		self.placeholder_tree = self.cfg.empty_tree

		# Absolute path to the working directory.
		# index files (".git.index<index_seq>") and .gitattributes files will be placed there
		self.git_index_directory = workdir
		self.index_seq = 0
		self.workdir_seq = 0
		if workdir:
			workdir.mkdir(parents=True, exist_ok = True)

		self.git_env = self.make_git_env()

		# Null tree SHA1
		self.initial_git_tree = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'
		self.gitattributes_sha1 = None

		# Full ref name for Git branch or tag for this branch
		self.refname = branch_map.refname
		self.alt_refname = branch_map.alt_refname

		if not getattr(self.proj_tree.options, 'create_revision_refs', False):
			self.revisions_ref = None
		elif branch_map.revisions_ref:
			self.revisions_ref = branch_map.revisions_ref
		elif self.refname.startswith('refs/heads/'):
			self.revisions_ref = branch_map.refname.replace('refs/heads/', 'refs/revisions/', 1)
		else:
			self.revisions_ref = branch_map.refname.replace('refs/', 'refs/revisions/', 1)

		self.init_head_rev()

		tagname = None
		for refname in [self.refname, self.alt_refname]:
			if refname and refname in proj_tree.append_to_refs:
				info = proj_tree.append_to_refs[refname]
				print('Found commit %s on previous refname "%s" to attach path "%s"'
					%(info.commit, refname, self.path), file=self.proj_tree.log_file)
				if self.HEAD.commit is not None and self.HEAD.commit != info.commit:
					print('WARNING: Two different commits found to attach path "%s"' % self.path, file=self.proj_tree.log_file)
				else:
					self.HEAD.commit = info.commit
					self.HEAD.committed_git_tree = info.tree
				if info.type == 'tag':
					tagname = refname
				proj_tree.append_to_refs.pop(refname, None)

				if self.add_tree_prefix:
					# Will detect the prefix at the first commit
					self.tree_prefix = None

		if tagname:
			info = self.git_repo.tag_info(tagname)
			if info:
				self.stage.props_list = [
					revision_props(0, info.log, author_props(info.author, info.email), info.date)]

		return

	def init_head_rev(self):
		HEAD = project_branch_rev(self)
		HEAD.staged_git_tree = self.initial_git_tree

		if self.inherit_mergeinfo:
			parent_mergeinfo, found_path = self.find_mergeinfo(find_inherited=True)
			if parent_mergeinfo:
				HEAD.mergeinfo.add_mergeinfo(parent_mergeinfo)
				HEAD.tree_mergeinfo.set_mergeinfo('..', HEAD.mergeinfo)

		self.HEAD = HEAD
		self.stage = project_branch_rev(self, HEAD)
		return

	def make_gitattributes_tree(self, tree, prev_tree):
		if self.git_index_directory is None:
			return

		if prev_tree is not self.proj_tree.empty_tree:
			self.workdir_seq += 1
			self.git_env = self.make_git_env()

		h = hashlib.sha1()

		# Check out all .gitattributes files from the injected list and the tree
		for path, obj in *self.inject_files.items(), *tree:
			if not obj.is_file() or not path.endswith('.gitattributes'):
				continue
			# Strip the filename
			directory = path[0:-len('.gitattributes')]
			if not directory:
				pass
			elif directory.endswith('/'):
				Path.mkdir(self.git_working_directory.joinpath(directory), parents=True, exist_ok = True)
			else:
				continue
			self.git_working_directory.joinpath(path).write_bytes(obj.pretty_data)
			h.update(b"%s\t%b" % (path.encode(), obj.data_sha1))
			continue

		self.gitattributes_sha1 = h.digest()
		return

	## Adds a parent branch, which will serve as the commit's parent.
	# If multiple revisions from a branch are added as a parent, highest revision is used for a commit
	# the branch also inherits all merged sources from the parent revision
	def add_branch_to_merge(self, source_branch, rev_to_merge):
		self.stage.add_branch_to_merge(source_branch, rev_to_merge)
		return

	def tree_is_similar(self, source):
		return self.HEAD.tree_is_similar(source)

	def find_mergeinfo(self, path="", rev=-1, find_inherited=False):
		return self.proj_tree.find_mergeinfo(self.path + path, rev, find_inherited)

	def find_tree_mergeinfo(self, path="", rev=-1, inherit=True, recurse_tree=False):
		return self.proj_tree.find_tree_mergeinfo(self.path + path, rev, inherit, recurse_tree)

	def set_orphan_parent(self, branch):
		if branch is None or not self.link_orphans:
			return

		if self.orphan_parent is None:
			self.orphan_parent = branch
		return

	def add_copy_source(self, copy_path, target_path, copy_rev, copy_branch=None):
		return self.stage.add_copy_source(copy_path, target_path, copy_rev, copy_branch)

	def set_rev_info(self, rev, rev_info):
		# get the head commit
		if not self.revisions:
			self.first_revision = rev
		elif rev < self.first_revision:
			return
		rev -= self.first_revision
		total_revisions = len(self.revisions)
		if rev < total_revisions:
			self.revisions[rev] = rev_info
			return
		if rev > total_revisions:
			self.revisions += self.revisions[-1:] * (rev - total_revisions)
		self.revisions.append(rev_info)
		return

	def get_revision(self, rev=-1):
		if rev <= 0 or not self.revisions:
			# get the head commit
			return self.HEAD
		rev -= self.first_revision
		if rev < 0 or not self.revisions:
			return None
		if rev >= len(self.revisions):
			return self.revisions[-1]
		return self.revisions[rev]

	### make_git_env sets up a map with GIT_INDEX_FILE and GIT_WORKING_DIR items,
	# to be used as environment for Git invocations
	def make_git_env(self):
		if self.git_index_directory:
			self.git_working_directory = self.git_index_directory.joinpath(str(self.workdir_seq))
			self.git_working_directory.mkdir(parents=True, exist_ok = True)

			return self.git_repo.make_env(
					work_dir=str(self.git_working_directory),
					index_file=str(self.git_index_directory.joinpath(".git.index" + str(self.index_seq))))
		return {}

	def set_head_revision(self, revision):
		rev_info = self.stage.set_revision(revision)
		if rev_info is None:
			return None
		self.set_rev_info(rev_info.rev, rev_info)
		return rev_info

	### The function makes a commit on this branch, using the properties from
	# history_revision object to set the commit message, date and author
	# If there is no changes, and this is a tag
	def prepare_commit(self, revision):
		rev_info = self.set_head_revision(revision)
		if rev_info is None:
			# The branch haven't been re-created after deletion
			# (could have happened on 'replace' command)
			return

		HEAD = self.HEAD
		self.HEAD = rev_info

		git_repo = self.git_repo
		if git_repo is None:
			self.stage = project_branch_rev(self, rev_info)
			HEAD.ready()
			return

		rev_info.log_file = self.proj_tree.log_file
		rev_info.log_file.add_dependency(rev_info)

		if self.tree_prefix is None:
			# need to detect the prefix by comparing pathnames of last git tree with pathnames of the new svn tree
			self.tree_prefix = find_tree_prefix(HEAD.committed_git_tree, rev_info.tree, git_repo)

		rev_info.build_stagelist(HEAD)

		# Can only make the next stage rev after done with building the stagelist
		# and processing the parent revision
		self.stage = project_branch_rev(self, rev_info)

		# If this branch is suspended because it's got child branches,
		# the rev_info will be marked as depending on the current branch
		if self.block_commits:
			rev_info.add_dependency(self.block_commits)
			self.block_commits = None

		assert(rev_info.tree is not None)
		self.proj_tree.commits_to_make += 1
		rev_info.set_async_func(self.finalize_commit, rev_info)

		# The newly built HEAD is not marked ready yet. Only previous HEAD is ready
		HEAD.ready()

		return

	def finalize_commit(self, rev_info):
		git_repo = self.git_repo

		parent_commits = []
		parent_git_tree = self.initial_git_tree
		prev_git_tree = self.initial_git_tree
		parent_tree = None
		commit = None

		# Check for fast forward
		if len(rev_info.parents) == 2:
			parent_rev = rev_info.parents[1]
			if parent_rev.committed_git_tree == rev_info.staged_git_tree and parent_rev.committed_git_tree != self.initial_git_tree:
				# Check if the first parent commit is a direct ancestor of this
				merged_to_parent_rev = parent_rev.get_merged_revision(rev_info)
				if merged_to_parent_rev is not None and \
					merged_to_parent_rev.walk_back_empty_revs() is rev_info.parents[0].walk_back_empty_revs():
					print("FAST FORWARD: Merge of %s;r%s to %s;r%s"
						% (parent_rev.branch.path, parent_rev.rev,
							rev_info.branch.path, rev_info.rev), file=rev_info.log_file)
					rev_info.parents.pop(0)

		need_commit = rev_info.need_commit
		skip_commit = rev_info.skip_commit
		base_rev = None
		for parent_rev in rev_info.parents:
			if parent_rev.commit is None:
				if skip_commit is None \
					and parent_rev.committed_git_tree == parent_rev.branch.initial_git_tree \
					and rev_info.staged_git_tree != parent_rev.committed_git_tree:
						need_commit = True
				continue
			if parent_rev.commit not in parent_commits:
				parent_commits.append(parent_rev.commit)
				if base_rev is None or base_rev.committed_git_tree == self.initial_git_tree:
					base_rev = parent_rev

		if base_rev is not None:
			parent_git_tree = base_rev.committed_git_tree
			prev_git_tree = base_rev.staged_git_tree
			parent_tree = base_rev.committed_tree
			commit = base_rev.commit

		if need_commit:
			for merge_rev in rev_info.merge_children:
				if merge_rev.commit and merge_rev.commit not in parent_commits:
					rev_info.parents.append(merge_rev)
					parent_commits.append(merge_rev.commit)

		# If the tree haven't changed, don't push the commit.
		# changes_present might have been set because the previous index was empty
		if len(parent_commits) > 1:
			need_commit = True
		elif rev_info.staged_git_tree == parent_git_tree:
			need_commit = False
		elif skip_commit is None and rev_info.changes_present:
			need_commit = True
		# if there are no changes in this revision, other than child branches changes,
		# link this rev info as dependent on this branch. Do not stage changes yet

		if need_commit:
			rev_props = rev_info.get_commit_revision_props(base_rev)
			author_info = rev_props.author_info

			commit = git_repo.commit_tree(rev_info.staged_git_tree, parent_commits, rev_props.log,
					author_name=author_info.author, author_email=author_info.email, author_date=rev_props.date,
					committer_name=author_info.author, committer_email=author_info.email, committer_date=rev_props.date,
					env=self.git_env)

			if self.proj_tree.log_commits:
				commit_str = self.git_repo.show(commit, "--raw", "--parents", "--no-decorate", "--abbrev-commit")
			else:
				commit_str = ''

			commit_str = "\nCOMMIT:%s REF:%s PATH:%s;%s\n" % (commit, self.refname, self.path, rev_info.rev)
			rev_info.log_file.write(commit_str)

			# Make a ref for this revision in refs/revisions namespace
			if self.revisions_ref:
				self.update_ref('%s/r%s' % (self.revisions_ref, rev_info.rev), commit, log_file=rev_info.log_file.revision_ref)

			rev_info.rev_commit = commit	# commit made on this revision, not inherited
			rev_info.committed_git_tree = rev_info.staged_git_tree
			rev_info.committed_tree = rev_info.tree
			self.proj_tree.commits_made += 1
		else:
			self.proj_tree.commits_to_make -= 1
			# Not making a commit yet, carry things over to the next
			next_rev = rev_info.next_rev
			for merge_child_rev in rev_info.merge_children:
				for merge_child_next_rev in next_rev.merge_children:
					if merge_child_next_rev.branch == merge_child_rev.branch:
						# The next revision merges this child branch
						break
				else:
					# The next revision doesn't merge this child branch
					# FIXME: check revisions
					next_rev.merge_children.append(merge_child_rev)
				if rev_info.need_commit:
					next_rev.need_commit = True

			if rev_info.merge_children:
				# Carry the revision properties over to the next commit
				if rev_info.staged_git_tree == parent_git_tree:
					# If there are no changes in the tree for this revision, discard the current revision log
					rev_info.props_list.pop(0)
				next_rev.props_list += rev_info.props_list

			# Carry the revision properties over to the next commit
			elif skip_commit is not None:
				if rev_info.staged_git_tree == prev_git_tree:
					# If there are no changes in the tree for this revision, discard the current revision props
					rev_info.props_list.pop(-1)
				# The skipped commit message gets prepended to the next revision,
				# Replace next revision props
				elif skip_commit.message is not None:
					rev_info.props_list[-1].log = log_to_paragraphs(skip_commit.message)
				elif not rev_info.props_list[-1].log:
					# If there's no message, discard the current revision props
					rev_info.props_list.pop(-1)
				next_rev.props_list = rev_info.props_list + next_rev.props_list

			rev_info.committed_git_tree = parent_git_tree
			rev_info.committed_tree = parent_tree

		rev_info.commit = commit
		rev_info.props_list = None
		return

	def stage_changes(self, stagelist, git_env):
		git_process = self.git_repo.update_index(git_env)
		pipe = git_process.stdin
		path_prefix = self.tree_prefix

		for item in stagelist:
			if path_prefix:
				item.path = path_prefix + item.path
			if item.obj is None:
				# a path is deleted
				pipe.write(b"000000 0000000000000000000000000000000000000000 0\t%s\n" % bytes(item.path, encoding='utf-8'))
				continue
			# a path is created or replaced
			pipe.write(b"%06o %s 0\t%s\n" % (item.mode, bytes(item.obj.get_git_sha1(), encoding='utf-8'), bytes(item.path, encoding='utf-8')))

		pipe.close()
		git_process.wait()

		return

	def get_file_mode(self, path, obj):
		if obj.is_dir():
			return 0o40000

		if obj.is_symlink():
			return 0o120000

		for (match_list, mode) in self.cfg.chmod_specifications:
			if match_list.match(path):
				return 0o100000|mode

		if obj.svn_executable is not None:
			return 0o100755
		return 0o100644

	def ignore_file(self, path):
		ignore = self.ignore_files.fullmatch(path)
		if ignore is None:
			ignore = self.cfg.ignore_files.fullmatch(self.path + path)
		return ignore

	def hash_object(self, data, path, sha1, fmt, git_env, log_file):
		if fmt is not None:
			def error_handler(s):
				print("WARNING: file %s:\n\t%s" % (self.path + path, s), file=log_file)
				return

			global TOTAL_FILES_REFORMATTED, TOTAL_BYTES_IN_FILES_REFORMATTED
			TOTAL_FILES_REFORMATTED += 1
			TOTAL_BYTES_IN_FILES_REFORMATTED += len(data)
			data = format_files.format_data(data, fmt, error_handler)
		# git_repo.hash_object will use the current environment from rev_info,
		# to use the proper .gitattributes worktree
		git_sha1 = self.git_repo.hash_object(data, path, env=git_env)
		self.proj_tree.sha1_map[sha1] = git_sha1
		return git_sha1

	def preprocess_blob_object(self, obj, node_path):
		proj_tree = self.proj_tree
		log_file = proj_tree.log_file
		# Cut off the branch path to make relative paths
		path = node_path.removeprefix(self.path)

		if self.ignore_file(path):
			if proj_tree.git_repo is None and proj_tree.options.log_dump:
				print('IGNORED: File %s' % (node_path), file=log_file)
				# With git repository, IGNORED files are printed during staging
			return obj

		if obj.is_symlink():
			return obj

		if getattr(proj_tree.options, 'replace_svn_keywords', False):
			obj = obj.expand_keywords(proj_tree.HEAD(), node_path)

		# path is relative to the branch root
		for fmt in self.format_specifications:
			match = fmt.paths.fullmatch(path)

			if not match:
				# fullmatch can return None and False
				if match is False and proj_tree.log_formatting_verbose and fmt.style:
					# This path is specifically excluded from this format specification
					print("FORMATTING: file \"%s\": explicitly excluded from format %s in branch \"%s\""
									% (path, fmt.format_str, self.path), file=log_file)
				continue

			if not fmt.style:
				# This format specification is setup to exclude it from formatting
				fmt = None
				if proj_tree.log_formatting_verbose:
					print("FORMATTING: file \"%s\": explicitly excluded from processing in branch \"%s\""
									% (path, self.path), file=log_file)
			elif proj_tree.log_formatting:
				print("FORMATTING: file \"%s\" with format %s in branch \"%s\""
									% (path, fmt.format_str, self.path), file=log_file)
			break
		else:
			# No match in per-branch specifications
			for fmt in self.cfg.format_specifications:
				# node_path is relative to the root of the source repository
				# Format paths are relative to the source root
				match = fmt.paths.fullmatch(node_path)

				if not match:
					# fullmatch can return None and False
					if match is False and proj_tree.log_formatting_verbose and fmt.style:
						# This path is specifically excluded from this format specification
						print("FORMATTING: file \"%s\": explicitly excluded from format %s" % (node_path, fmt.format_str), file=log_file)
					continue

				if not fmt.style:
					# This format specification is setup to exclude it from formatting
					fmt = None
					if proj_tree.log_formatting_verbose:
						print("FORMATTING: file \"%s\": explicitly excluded from processing" % (node_path), file=log_file)
				elif proj_tree.log_formatting:
					print("FORMATTING: file \"%s\" with format %s" % (node_path, fmt.format_str), file=log_file)
				break
			else:
				fmt = None

		if fmt is not None:
			if obj.git_attributes.get('formatting') != fmt.format_tag:
				obj = obj.make_unshared()
				obj.git_attributes['formatting'] = fmt.format_tag
		elif 'formatting' in obj.git_attributes:
			obj = obj.make_unshared()
			obj.git_attributes.pop('formatting')

		# Find git attributes - TODO fill cfg.gitattributes
		for attr in self.cfg.gitattributes:
			if attr.pattern.fullmatch(path) and obj.git_attributes.get(attr.key) != attr.value:
				obj = obj.make_unshared()
				obj.git_attributes[attr.key] = attr.value

		obj = proj_tree.finalize_object(obj)
		obj.fmt = fmt	# AFTER finalize_object
		return obj

	def finalize_deleted(self, rev, sha1, props):
		if not sha1:
			return

		log_file = self.proj_tree.log_file
		refname = self.refname
		alt_refname = self.alt_refname
		tagname = None
		if props and props.log:
			# If there's a pending message, make an annotated tag
			if refname.startswith('refs/tags/'):
				tagname = refname
			elif alt_refname and alt_refname.startswith('refs/tags/'):
				tagname = alt_refname
			else:
				print('Deleted revision %s on SVN path "%s" discards the following commit messages:\n\t%s"'
					% (rev, self.path, '\n\t'.join('\n\n'.join(props.log).splitlines())),
					file=log_file)

		if tagname:
			refname = self.create_tag(tagname + ('_deleted@r%s' % rev), sha1, props)
		elif refname:
			refname = self.update_ref(refname + ('_deleted@r%s' % rev), sha1)

		if refname:
			print('Deleted revision %s on path "%s" is preserved as refname "%s"'
				% (rev, self.path, refname), file=log_file)
		else:
			print('Deleted revision %s on path "%s" not merged to any path or mapped to refname'
				% (rev, self.path), file=log_file)
		return

	def delete(self, revision):
		if not self.HEAD.tree and not self.HEAD.commit:
			# This also will bail out if branch delete happens twice in a revision
			return

		print('Branch at SVN path "%s" deleted at revision %s\n' %
				(self.path, revision.rev), file=self.proj_tree.log_file)

		self.HEAD.mark_need_commit()
		self.add_dependency(self.HEAD)
		self.HEAD.ready()
		rev_info = self.stage
		rev_info.rev = revision.rev
		rev_info.add_revision_props(revision)

		# Set the deleted revision now to propagate it until the branch is reinstated
		self.set_rev_info(rev_info.rev, rev_info)

		self.proj_tree.deleted_revs.append(rev_info)

		# Start with fresh index
		self.index_seq += 1
		self.git_env = self.make_git_env()

		self.init_head_rev()

		return

	def finalize(self, merged_revs_dict):

		sha1 = self.HEAD.commit
		if not sha1:
			if self.HEAD.tree:
				# Check for refname conflict
				refname = self.cfg.map_ref(self.refname)
				refname = self.proj_tree.make_unique_refname(refname, self.path, self.proj_tree.log_file)
			# else: The branch was deleted
			return

		refname = self.refname
		# if it's a branch, alt_refname makes an annotated tag for any revision messages
		# not used for commits
		alt_refname = self.alt_refname

		# Name of an annotated tag to make, if needed
		props = self.stage.get_combined_revision_props(empty_message_ok=True)
		if props and props.log:
			# If there's a pending message, make an annotated tag
			if refname.startswith('refs/tags/'):
				tagname = self.create_tag(refname, sha1, props)
				refname = None
			elif alt_refname and alt_refname.startswith('refs/tags/'):
				tagname = self.create_tag(alt_refname, sha1, props)
				alt_refname = None
			else:
				tagname = None

			if not tagname:
				print('HEAD on path "%s" discards the following commit messages:\n    %s"'
					% (self.path, '\n    '.join('\n'.join(props.log).splitlines())), file=self.proj_tree.log_file)

		if alt_refname and alt_refname.startswith('refs/heads/') \
			and self.HEAD.get_revision_merged_at(merged_revs_dict) is None:
			# If a tag directory has had commits, make a branch for it, too
			self.update_ref(alt_refname, sha1)

		if refname:
			self.update_ref(refname, sha1)

		return

	def update_ref(self, refname, sha1, log_file=None):
		refname = self.cfg.map_ref(refname)
		return self.proj_tree.update_ref(refname, sha1, self.path, log_file)

	def create_tag(self, tagname, sha1, props, log_file=None):
		tagname = self.cfg.map_ref(tagname)
		return self.proj_tree.create_tag(tagname, sha1, props, self.path, log_file)

	def ready(self):
		# This node will be executed when the last commit of the branch is done
		self.HEAD.mark_need_commit()

		self.add_dependency(self.HEAD)
		self.HEAD.ready()

		self.release_all_dependents()

		self.executor.add_dependency(self)

		return super().ready()

def make_git_object_class(base_type):
	class git_object(base_type):
		def __init__(self, src = None, properties=None):
			super().__init__(src, properties)
			if src:
				self.git_attributes = src.git_attributes.copy()
			else:
				# These attributes also include prettyfication and CRLF normalization attributes:
				self.git_attributes = {}
			return

		# return hashlib SHA1 object filled with hash of prefix, data SHA1, and SHA1 of all attributes
		def make_svn_hash(self):
			h = super().make_svn_hash()

			# The dictionary provides the list in order of adding items
			# Make sure the properties are hashed in sorted order.
			gitattrs = list(self.git_attributes.items())
			gitattrs.sort()
			for (key, data) in gitattrs:
				h.update(b'ATTR: %s %d\n' % (key.encode(encoding='utf-8'), len(data)))
				h.update(data)

			return h

		def print_diff(obj2, obj1, path, fd):
			super().print_diff(obj1, path, fd)

			if obj1 is None:
				for key in obj2.git_attributes:
					print("  GIT ATTR: %s=%s" % (key, obj2.git_attributes[key]), file=fd)
				return

			# Print changed attributes

			if obj1.git_attributes != obj2.git_attributes:
				for key in obj1.git_attributes:
					if key not in obj2.git_attributes:
						print("  GIT ATTR DELETED: " + key, file=fd)
				for key in obj2.git_attributes:
					if key not in obj1.git_attributes:
						print("  GIT ATTR ADDED: %s=%s" % (key, obj2.git_attributes[key]), file=fd)
				for key in obj1.git_attributes:
					if key in obj2.git_attributes and obj1.git_attributes[key] != obj2.git_attributes[key]:
						print("  GIT ATTR CHANGED: %s=%s" % (key, obj2.git_attributes[key]), file=fd)
			return

	return git_object

class git_tree(make_git_object_class(svn_tree)):

	class item:
		def __init__(self, name, obj, mode=None):
			self.name = name
			self.object = obj
			if obj.is_file() and mode:
				self.mode = mode
			return

class git_blob(make_git_object_class(svn_blob)):
	def __init__(self, src = None, properties=None):
		super().__init__(src, properties)
		# this is git sha1, produced by git-hash-object, as 40 chars hex string.
		# it's not copied during copy()
		self.git_sha1 = None
		if src is not None:
			self.fmt = src.fmt
		else:
			self.fmt = None
		return

	def get_git_sha1(self):
		return str(self.git_sha1)

	def is_symlink(self):
		return self.svn_special is not None \
			and self.data.startswith(b'link ')

class log_serializer(dependency_node):

	def __init__(self, *dep_nodes, log_output_file=None, log_refs_file=None, executor=None):
		super().__init__(*dep_nodes, executor=executor)

		if dep_nodes and type(dep_nodes[0]) is log_serializer:
			prev_serializer = dep_nodes[0]
			self.prev_tree = prev_serializer.curr_tree
			self.curr_tree = self.prev_tree
		else:
			self.curr_tree = None
			self.prev_tree = None

		# self.skipped_revs (if not None) is a list.
		# Each item is a tuple of: revision list, and has_nodes.
		# Each revision list contains tuples of (first_rev, last_rev)
		self.skipped_revs = None
		self.dump_revision = None
		self.need_dump = False
		self.log_output_file = log_output_file
		self.log_refs_file = log_refs_file
		self.newlines = log_output_file.newlines
		self.log_file = io.StringIO()
		self.revision_ref = io.StringIO()
		return

	def set_revision_to_dump(self, revision, log_revs, need_dump, has_nodes):
		self.dump_revision = revision.dump_revision

		self.curr_tree = revision.tree
		if not log_revs:
			self.prev_tree = self.curr_tree

		self.need_dump = need_dump
		if need_dump:
			return

		rev = revision.rev
		# self.skipped_revs is a list.
		# Each item is a tuple of: revision list, and has_nodes.
		# Each revision list contains tuples of (first_rev, last_rev)
		if self.skipped_revs is None:
			self.skipped_revs = [([(rev,rev)], has_nodes)]
			return

		last_skipped_revs, last_has_nodes = self.skipped_revs[-1]
		if last_has_nodes != has_nodes:
			self.skipped_revs.append(([(rev, rev)], has_nodes))
			return
		if last_skipped_revs[-1][1] + 1 == rev:
			last_skipped_revs[-1] = (last_skipped_revs[-1][0], rev)
		else:
			last_skipped_revs.append((rev, rev))

		return

	def write(self, s):
		if self.log_file:
			return self.log_file.write(s)
		return self.log_output_file.write(s)

	def do_dump(self):

		if self.log_output_file is None:
			return

		# Print skipped revisions
		if self.skipped_revs is not None:
			for revisions, has_nodes in self.skipped_revs:
				print("%s REVISION%s: %s" % (
					"SKIPPED" if has_nodes else "EMPTY",
					"S" if len(revisions) > 1 else "",
					ranges_to_str(revisions)), file=self.log_output_file)
			self.skipped_revs = None

		if self.dump_revision is not None and self.need_dump:
			self.dump_revision.print(self.log_output_file)
			self.dump_revision = None

		if self.prev_tree is not self.curr_tree:
			diffs = [*type(self.prev_tree).compare(self.prev_tree, self.curr_tree, expand_dir_contents=True)]
			if len(diffs):
				print("Comparing with previous revision:", file=self.log_output_file)
				print_diff(diffs, self.log_output_file)
				print("", file=self.log_output_file)

		if self.log_file:
			self.log_output_file.write(self.log_file.getvalue())
			self.log_file = None

		if self.revision_ref is not None and self.log_refs_file:
			self.log_refs_file.write(self.revision_ref.getvalue())
			self.log_refs_file = None

		self.log_output_file = None
		return

	def complete(self):
		self.do_dump()
		return super().complete()

class project_history_tree(history_reader):
	BLOB_TYPE = git_blob
	TREE_TYPE = git_tree

	def __init__(self, options=None):
		super().__init__(options)

		self.options = options
		self.log_file = options.log_file
		self.log_serializer = None
		self.log_commits = getattr(options, 'log_commits', False)
		self.log_merges_verbose = getattr(options, 'log_merges_verbose', False)
		self.log_merges = self.log_merges_verbose or getattr(options, 'log_merges', False)
		self.log_formatting_verbose = getattr(options, 'log_formatting_verbose', False)
		self.log_formatting = self.log_formatting_verbose or getattr(options, 'log_formatting', False)

		# This is a tree of branches
		self.branches = path_tree()
		self.mapped_dirs = path_tree()
		# class path_tree iterates in the tree recursion order: from root to branches
		# branches_list will iterate in order in which the branches are created
		self.branches_list = []
		# Memory file to write revision ref updates
		self.revision_ref_log_file = io.StringIO()
		# This path tree is used to detect refname collisions, when a new branch
		# is created with an already existing ref
		self.all_refs = path_tree()
		self.prev_sha1_map = {}
		self.sha1_map = {}
		self.deleted_revs = []
		# authors_map maps revision.author to the author name and email
		# (name, email) are stored as tuple in the dictionary
		# Missing names are also added to the dictionary as <name>@localhost
		self.authors_map = {}
		self.unmapped_authors = []
		self.append_to_refs = {}
		self.prune_refs = {}
		# This is list of project configurations in order of their declaration
		self.project_cfgs_list = project_config.project_config.make_config_list(options.config,
											getattr(options, 'project_filter', []),
											project_config.project_config.make_default_config(options))

		path_filter = getattr(options, 'path_filter', [])
		if path_filter:
			self.path_filters = [project_config.path_list_match(*path_filter,
											match_dirs=True, split=',')]
		else:
			# Make path filters from projects
			self.path_filters = [cfg.paths for cfg in self.project_cfgs_list]

		target_repo = getattr(options, 'target_repo', None)
		if target_repo:
			self.git_repo = git_repo.GIT(target_repo)
			# Get absolute path of git-dir
			git_dir = self.git_repo.get_git_dir(True)
			self.git_working_directory = Path(git_dir, "svn_temp")
		else:
			self.git_repo = None
			self.git_working_directory = None

		self.commits_to_make = 0
		self.prev_commits_to_make = None
		self.commits_made = 0
		self.branch_dir_index = 1	# Used for branch working directory
		self.total_branches_made = 0
		self.total_tags_made = 0
		self.total_refs_to_update = 0
		self.prev_commits_made = None

		# Directory of actions to perform at given revision, keyed by integer revision number.
		self.revision_actions = {}
		for cfg in self.project_cfgs_list:
			# Make blobs for files to be injected
			for file in cfg.inject_files:
				file.blob = self.make_blob(file.data, None)

			for rev, actions in cfg.revision_actions.items():
				self.revision_actions.setdefault(rev, []).extend(actions)

			if cfg.empty_placeholder_name:
				cfg.empty_tree = self.finalize_object(self.TREE_TYPE().set(cfg.empty_placeholder_name,
								self.make_blob(bytes(cfg.empty_placeholder_text, encoding='utf-8'), None)))
			else:
				cfg.empty_tree = None

			for fmt in cfg.format_specifications:
				if options.retab_only:
					fmt.retab_only = True
				elif options.skip_indent_format:
					fmt.skip_indent_format = True

		for extract_file in getattr(options, 'extract_file', []):
			extract_file_split = extract_file[0].partition(',')
			extract_file_path = extract_file_split[0]
			extract_file_rev = re.fullmatch(r'r?(\d+)', extract_file_split[2])
			if extract_file_rev is None:
				raise Exception_cfg_parse('Invalid --extract-file argument "%s". Must be formatted as <SVN path>,r<revision>'
							% (extract_file))

			actions = self.revision_actions.setdefault(int(extract_file_rev[1]), [])
			actions.append(project_config.svn_revision_action(b'extract', extract_file[1], copyfrom_path=extract_file_path))

		self.executor = async_executor()
		self.futures_executor=concurrent.futures.ThreadPoolExecutor(max_workers=min(4, os.cpu_count()+ 1))
		# Serialize all write-tree invocations into a single worker thread
		self.write_tree_executor=concurrent.futures.ThreadPoolExecutor(max_workers=1)

		refs_list = getattr(options, 'prune_refs', None)
		if self.git_repo and refs_list:
			if refs_list == ['']:
				# Create pruning refs list from the projects
				refs_list = [cfg.refs for cfg in self.project_cfgs_list]
			else:
				refs_list = [project_config.refs_list_match(*refs_list, split=',')]
			self.load_refs_to_prune(refs_list)

		if options.sha1_map:
			self.load_sha1_map(options.sha1_map)

		if options.authors_map:
			self.load_authors_map(options.authors_map)

		if options.append_to_refs:
			self.load_prev_refs(options.append_to_refs, refs_list)

		return

	def shutdown(self):
		self.futures_executor.shutdown(cancel_futures=True)
		self.write_tree_executor.shutdown(cancel_futures=True)

		self.git_repo.shutdown()
		shutil.rmtree(self.git_working_directory, ignore_errors=True)
		self.git_working_directory = None
		return

	def make_log_serializer(self, *prev_serializer, executor=None):
		for s in prev_serializer:
			s.ready()

		return log_serializer(*prev_serializer,
							log_output_file=self.options.log_file,
							log_refs_file=self.revision_ref_log_file,
							executor=executor)

	def next_log_serializer(self):
		if self.log_serializer is not None:
			self.log_serializer = self.make_log_serializer(self.log_serializer)
			self.log_file = self.log_serializer
		return

	## Finds an existing branch for the path and revision
	# @param path - the path to find a branch.
	#  The target branch path will be a prefix of path argument
	# @param rev - revision
	# The function is used to find a merge parent.
	# If a revision was not present in a branch, return None.
	def find_branch_rev(self, path, rev):
		# find project, find branch from project
		branch = self.find_branch(path)
		if branch:
			return branch.get_revision(rev)
		return None

	def find_mergeinfo(self, path, rev, find_inherited=False):
		if rev < 0:
			revision = self.HEAD()
		else:
			revision = self.get_revision(rev)
		path_mergeinfo = mergeinfo()
		if revision is not None:
			found_path = path_mergeinfo.find_path_mergeinfo(revision.tree, path, skip_first=find_inherited)
		else:
			found_path = None

		return path_mergeinfo, found_path

	def find_tree_mergeinfo(self, path, rev, inherit=True, recurse_tree=False):
		if rev < 0:
			revision = self.HEAD()
		else:
			revision = self.get_revision(rev)
		new_tree_mergeinfo = tree_mergeinfo()
		if revision is not None:
			new_tree_mergeinfo.load_tree(revision.tree, path, inherit, recurse_tree)

		return new_tree_mergeinfo

	## Finds a base branch for the new path and current revision
	# @param path - the path to find a branch.
	#  The target branch path will be a prefix of 'path'
	def find_branch(self, path, match_full_path=False):
		return self.branches.find_path(path, match_full_path)

	def all_branches(self) -> Iterator[project_branch]:
		return (node.object for node in self.branches if node.object is not None)

	def set_branch_changed(self, branch):
		if branch not in self.branches_changed:
			self.branches_changed.append(branch)
			branch.set_head_revision(self.HEAD())
		return

	def get_branch_map(self, path):
		if not path.endswith('/'):
			# Make sure there's a slash at the end
			path += '/'

		mapped = self.mapped_dirs.get_mapped(path, match_full_path=True)
		if mapped is False:
			return None

		for cfg in self.project_cfgs_list:
			branch_map = cfg.map_path(path)
			if branch_map is None:
				continue

			if not branch_map.refname:
				# This path is blocked from creating a branch on it
				if branch_map.path == path:
					print('Directory "%s" mapping with globspec "%s" in config "%s":\n'
								% (path, branch_map.globspec, cfg.name),
							'         Blocked from creating a branch',
							file=self.log_file)
				break

			branch_map.cfg = cfg
			return branch_map
		else:
			# See if any parent directory is explicitly unmapped.
			# Note that as directories get added, the parent directory has already been
			# checked for mapping
			if self.mapped_dirs.get_mapped(path, match_full_path=False) is None:
				print('Directory mapping: No map for "%s" to create a branch' % path, file=self.log_file)

		# Save the unmapped directory
		self.mapped_dirs.set_mapped(path, False)
		return None

	## Adds a new branch for path in this revision, possibly with source revision
	# The function must not be called when a branch already exists
	def add_branch(self, branch_map, parent_branch=None):
		print('Directory "%s" mapping with globspec "%s" in config "%s":'
				% (branch_map.path, branch_map.globspec, branch_map.cfg.name),
				file=self.log_file)

		if self.git_working_directory:
			git_workdir = Path(self.git_working_directory, str(self.branch_dir_index))
			self.branch_dir_index += 1
		else:
			git_workdir = None

		if branch_map.link_orphans is None:
			branch_map.link_orphans = getattr(self.options, 'link_orphan_revs', False)

		if branch_map.add_tree_prefix is None:
			branch_map.add_tree_prefix = getattr(self.options, 'add_branch_prefix', False)

		branch = project_branch(self, branch_map, git_workdir, parent_branch)

		if branch.refname:
			print('    Added new branch %s' % (branch.refname), file=self.log_file)
		else:
			print('    Added new unnamed branch', file=self.log_file)

		if parent_branch:
			if branch_map.merge_to_parent:
				print('    Merged to parent branch on path %s' % (parent_branch.path), file=self.log_file)
			else:
				print('    Excluded from parent branch on path %s' % (parent_branch.path), file=self.log_file)

		self.branches.set(branch_map.path, branch)
		self.branches.set_mapped(branch_map.path, True)
		self.branches_list.append(branch)
		self.mapped_dirs.set_mapped(branch_map.path, True)

		return branch

	def make_unique_refname(self, refname, path, log_file):
		if not refname:
			return refname
		new_ref = refname
		# Possible conflicts:
		# a) The terminal path element conflicts with an existing terminal tree element. Can add a number to it
		# b) The terminal path element conflicts with an existing non-terminal tree element (directory). Can add a number to it
		# c) The non-terminal path element conflicts with an existing terminal tree element (leaf). Impossible to resolve

		# For terminal elements, leaf if set to the 
		for i in range(1, 100):
			node = self.all_refs.get_node(new_ref, match_full_path=True)
			if node is None:
				# Full path doesn't match, but partial path may exist
				break
			# Full path matches, try next refname
			new_ref = refname + '___%d' % i
			i += 1
		else:
			print('WARNING: Unable to find a non-conflicting name for "%s",\n'
				  '\tTry to adjust the map configuration' % refname,
				file=log_file)
			return None

		if self.all_refs.find_path(new_ref, match_full_path=False):
			if not self.all_refs.get_used_by(new_ref, key=new_ref, match_full_path=False):
				was_used_by = self.all_refs.get_used_by(new_ref, match_full_path=False)
				self.all_refs.set_used_by(new_ref, new_ref, path, match_full_path=False)
				print('WARNING: Unable to find a non-conflicting name for "%s",\n'
					  '\tbecause the partial path is already a non-directory mapped by "%s".\n'
					  '\tTry to adjust the map configuration'
						% (refname, was_used_by[1]), file=log_file)
				return None
			if path is not None:
				print('WARNING: Refname "%s" is already used by "%s";'
					% (refname, self.all_refs.get_used_by(refname)[1]), file=log_file)
				print('         Remapped to "%s"' % new_ref, file=log_file)

		self.all_refs.set(new_ref, new_ref)
		self.all_refs.set_used_by(new_ref, new_ref, path, match_full_path=True)
		return new_ref

	def update_ref(self, ref, sha1, path, log_file=None):
		if log_file is None:
			log_file = self.log_file

		ref = self.make_unique_refname(ref, path, log_file)
		if not ref or not sha1:
			return ref

		print('WRITE REF: %s %s' % (sha1, ref), file=log_file)
		self.append_to_refs.pop(ref, "")

		if ref.startswith('refs/tags/'):
			self.total_tags_made += 1
		elif ref.startswith('refs/heads/'):
			self.total_branches_made += 1

		if ref in self.prune_refs:
			if sha1 == self.prune_refs[ref]:
				del self.prune_refs[ref]
				return ref

			del self.prune_refs[ref]

		self.git_repo.queue_update_ref(ref, sha1)
		self.total_refs_to_update += 1

		return ref

	def create_tag(self, tagname, sha1, props, path, log_file=None):
		if log_file is None:
			log_file = self.log_file

		tagname = self.make_unique_refname(tagname, path, log_file)
		if not tagname or not sha1:
			return tagname

		print('CREATE TAG: %s %s' % (sha1, tagname), file=log_file)

		self.git_repo.tag(tagname.removeprefix('refs/tags/'), sha1, props.log,
			props.author_info.author, props.author_info.email, props.date, '-f')
		self.total_tags_made += 1

		self.append_to_refs.pop(tagname, "")
		self.prune_refs.pop(tagname, "")

		return tagname

	def get_unmapped_directories(self):
		dirs = []
		get_directory_mapped_status(self.mapped_dirs, dirs)
		dirs.sort()
		return dirs

	# To adjust the new objects under this node with Git attributes,
	# we will override history_reader:make_blob
	def make_blob(self, data, node, properties=None):
		obj = super().make_blob(data, node, properties)
		return self.preprocess_blob_object(obj, node)

	def preprocess_blob_object(self, obj, node):
		if node is None:
			return obj

		branch = self.find_branch(node.path)
		if branch is None:
			directory = node.path.rsplit('/', 1)[0]
			self.mapped_dirs.set_used_by(directory, directory, True, match_full_path=False)
			return obj

		# New object has just been created
		return branch.preprocess_blob_object(obj, node.path)

	def copy_blob(self, src_obj, node, properties):
		obj = super().copy_blob(src_obj, node, properties)
		return self.preprocess_blob_object(obj, node)

	def apply_dir_node(self, node, base_tree):

		base_tree = super().apply_dir_node(node, base_tree)

		if node.action == b'add':
			node_branches_changed = []
			root_path = node.path
			if root_path:
				root_path += '/'

			for (path, obj) in base_tree.find_path(node.path):
				if not obj.is_dir():
					continue
				if obj.is_hidden():
					continue
				# Check if we need and can create a branch for this directory
				branch_map = self.get_branch_map(root_path + path)
				if not branch_map:
					continue

				path = branch_map.path
				branch = self.find_branch(path, match_full_path=True)
				new_branch = None
				if not branch:
					while True:
						split_path = path.rpartition('/')
						path = split_path[0]
						if not path:
							parent_branch = self.find_branch('/', match_full_path=True)
							break
						if not split_path[2]:
							continue
						# Find a parent branch. It should already be created,
						# because the tree iterator returns the parent tree before its subtrees
						parent_branch = self.find_branch(split_path[0], match_full_path=False)
						if parent_branch is not None:
							break
						continue
					branch = self.add_branch(branch_map, parent_branch)
					if not branch:
						continue
					new_branch = branch
					# link orphan branches. This is done by setting an orphan parent
					if not parent_branch \
							and node.copyfrom_path is None and self.branches_changed:
						branch.set_orphan_parent(self.branches_changed[-1])

				if branch in node_branches_changed:
					continue

				node_branches_changed.append(branch)

				if node.copyfrom_path is None:
					continue

				# root_path - directory to be added
				# branch.path
				source_path = node.copyfrom_path
				# node.path can either be inside the branch, or encompass the branch
				if node.path.startswith(branch.path):
					# the node path is inside the branch
					source_path = node.copyfrom_path
					target_path = node.path
				else:
					# the node path is either outside or on the same level as the branch
					# branch.path begins with '/'
					assert(branch.path[len(node.path):] == branch.path.removeprefix(node.path))
					path_suffix = branch.path.removeprefix(node.path)
					source_path = node.copyfrom_path + path_suffix
					target_path = branch.path
				if source_path and not source_path.endswith('/'):
					source_path += '/'

				source_branch = self.find_branch(source_path)

				if source_branch and new_branch and new_branch.add_tree_prefix:
					# The new branch may start from subdirectory of parent branch. Add subdirectory prefix
					new_branch.tree_prefix = source_branch.tree_prefix + source_path.removeprefix(source_branch.path)

				branch.add_copy_source(source_path, target_path, node.copyfrom_rev, source_branch)
				continue

			for branch in node_branches_changed:
				self.set_branch_changed(branch)

		return base_tree

	def filter_path(self, path, kind, base_tree):

		if kind == b'dir' and not path.endswith('/'):
			path += '/'
		elif kind is None:	# Deleting a tree or a file
			obj = base_tree.find_path(path)
			if obj is None or obj.is_dir() and not path.endswith('/'):
				path += '/'

		for path_filter in self.path_filters:
			if path_filter.match(path, True):
				return True;

		return False

	def apply_node(self, node, base_tree):

		self.revision_has_nodes = True
		if not self.filter_path(node.path, node.kind, base_tree):
			return base_tree

		self.revision_need_dump = True
		# Check if the copy source refers to a path filtered out
		if node.copyfrom_path is not None and not self.filter_path(node.copyfrom_path, node.kind, base_tree) and node.text_content is None:
			raise Exception_history_parse('Node Path="%s": Node-copyfrom-path "%s" refers to a filtered-out directory'
						% (node.path, node.copyfrom_path))

		if node.action == b'merge':
			branch = self.find_branch(node.path)
			if branch is None:
				raise Exception_history_parse("'merge' operation refers to path \"%s\" not mapped to any branch"
							% (node.path))

			if branch.path != node.path:
				print("WARNING: 'merge' operation target refers to a subdirectory \"%s\" under the branch directory \"%s\""
						% (node.path.removeprefix(branch.path), branch.path), file=self.log_file)

			if not self.filter_path(node.copyfrom_path, b'dir', None):
				raise Exception_history_parse("'merge' operation refers to source path \"%s\" filtered out by --filter-path command line option"
							% (node.copyfrom_path))

			source_branch = self.find_branch(node.copyfrom_path)
			if source_branch is None:
				raise Exception_history_parse("'merge' operation source \"%s\" not mapped to any branch"
							% (node.copyfrom_path))

			if source_branch.path != node.copyfrom_path:
				print("WARNING: 'merge' operation source is a subdirectory \"%s\" under the branch directory \"%s\""
						% (node.copyfrom_path.removeprefix(source_branch.path), source_branch.path), file=self.log_file)

			rev = node.copyfrom_rev
			rev_info = source_branch.get_revision(rev)
			if not rev_info or rev_info.rev is None:
				raise Exception_history_parse("'merge' operation refers to source revision \"%s\" not present at path %s"
							% (rev, node.copyfrom_path))

			print("MERGE PATH: Forcing merge of %s;r%s onto %s;r%s"
				%(source_branch.path, rev, branch.path, self.HEAD().rev),
				file=self.log_file)

			branch.add_branch_to_merge(source_branch, rev_info)
			self.set_branch_changed(branch)
			return base_tree

		# 'delete' action comes with no kind
		if node.action == b'delete' or node.action == b'hide' or node.action == b'replace':
			tree_node = self.branches.get_node(node.path, match_full_path=True)
			if tree_node is not None:
				# Recurse into all branches under this directory
				# The tree node iterator returns nodes recursively,
				# starting with the parent node (which also includes the very starting node)
				# We want to process them in such order that the parent node is the last
				# For this, we make a list out of the iterator, and get a reversed iterator of it
				for deleted_node in reversed(list(iter(tree_node))):
					deleted_branch = deleted_node.object
					if deleted_branch is None:
						continue
					deleted_branch.delete(self.HEAD())

					if deleted_branch.merge_parent:
						self.set_branch_changed(deleted_branch.merge_parent)

		base_tree = super().apply_node(node, base_tree)

		self.executor.run(existing_only=True)

		branch = self.find_branch(node.path)
		if branch is None:
			# this was a delete operation, or
			# the node was outside any defined project/branch path;
			# this change will not generate a commit on any ref
			return base_tree

		self.set_branch_changed(branch)

		if node.kind != b'file' or node.copyfrom_path is None:
			return base_tree

		source_branch = self.find_branch(node.copyfrom_path)
		if source_branch:
			source_rev = source_branch.get_revision(node.copyfrom_rev)
			if source_rev and source_rev.tree:
				# If the source tree is similar, the branches are related
				if not branch.recreate_merges.file_copy \
						or not branch.tree_is_similar(source_rev):
					source_branch = None

				# Node and source are both 'file' here
				branch.add_copy_source(node.copyfrom_path, node.path, node.copyfrom_rev,
						source_branch)

		return base_tree

	def apply_file_node(self, node, base_tree):
		base_tree = super().apply_file_node(node, base_tree)
		if node.action != b'delete' and node.action != b'hide':
			branch = self.find_branch(node.path)
			if branch:
				file = base_tree.find_path(node.path)
				base_tree = base_tree.set(node.path, file,
								mode=branch.get_file_mode(node.path, file))
		return base_tree

	def apply_revision(self, revision):
		# Apply the revision to the previous revision, checking if new branches are created
		# into commit(s) in the git repository.

		self.revision_has_nodes = False
		self.revision_need_dump = self.log_dump_all

		revision = super().apply_revision(revision)

		rev_actions = self.revision_actions.get(revision.rev, [])
		for rev_action in rev_actions:
			if rev_action.action == b'add':
				if revision.tree.find_path(rev_action.path):
					rev_action.action = b'change'
			elif rev_action.action == b'copy':
				src_revision = self.get_revision(rev_action.copyfrom_rev)
				if src_revision is None:
					raise Exception_history_parse(
						'<CopyPath> refers to non-existing source revision "%s"' % (rev_action.copyfrom_rev))
				src_node = src_revision.tree.find_path(rev_action.copyfrom_path)
				if src_node is None:
					raise Exception_history_parse('<CopyPath> refers to path "%s" not present in revision %s'
						% (rev_action.copyfrom_path, src_revision.rev))
				if src_node.is_dir():
					rev_action.kind = b'dir'
				else:
					rev_action.kind = b'file'

				if revision.tree.find_path(rev_action.path) is not None:
					rev_action.action = b'replace'
				else:
					rev_action.action = b'add'
			elif rev_action.action == b'delete':
				# hide the file or directory
				rev_action.action = b'hide'
				src_node = revision.tree.find_path(rev_action.path)
				if src_node is None:
					raise Exception_history_parse('<DeletePath> operation refers to non-existing path "%s"' % rev_action.path)
				if src_node.is_dir():
					rev_action.kind = b'dir'
				else:
					rev_action.kind = b'file'
			elif rev_action.action == b'merge':
				...
			elif rev_action.action == b'extract':
				file = revision.tree.find_path(rev_action.copyfrom_path)
				if file is None:
					raise Exception_history_parse('--extract-file refers to path "%s" not present in revision %s'
							% (rev_action.copyfrom_path, revision.rev))
				if not file.is_file():
					raise Exception_history_parse('--extract-file refers to path "%s" in revision %s which is not a file'
							% (rev_action.copyfrom_path, revision.rev))
				with open(rev_action.path, 'wb') as fd:
					fd.write(file.data)
				continue

			revision.tree = self.apply_node(rev_action, revision.tree)
			continue

		revision.tree = self.finalize_object(revision.tree)

		# self.revision_need_dump is set when dump_all is specified or a revision has non-ignored nodes
		# Such revision will show up in the dump (only dumped if verbose=dump)
		# self.revision_has_nodes is set if a revision has any nodes, some of them might have been ignored
		# If dump_all, all revisions are printed, even empty or those with all ignored nodes.
		# If not dump_all, only revisions with non-ignored nodes are printed.
		# If a revision has nodes, but they are ignored,
		# the revision(s) are printed as "SKIPPED REVISIONS:"
		if self.log_serializer is not None:
			self.log_serializer.set_revision_to_dump(revision,
					self.log_revs, self.revision_need_dump, self.revision_has_nodes)
			if self.revision_need_dump or (self.log_revs and self.revision_has_nodes):
				self.next_log_serializer()

		# Ensure the proper ordering of merged branches:
		branches_changed = []
		for branch in self.branches_changed:
			if branch not in branches_changed:
				branches_changed.append(branch)
			# Make sure this branch change is handled before its merge parent
			merge_parent = branch.merge_parent
			if merge_parent is not None:
				if merge_parent in branches_changed \
						and branches_changed[-1] is not merge_parent:
					branches_changed.remove(merge_parent)
				branches_changed.append(merge_parent)

		# Prepare commits
		for branch in branches_changed:
			branch.prepare_commit(revision)
			if branch.merge_parent:
				# Now we merge this new commit into the parent branch
				branch.merge_parent.add_branch_to_merge(branch, branch.HEAD)
			self.next_log_serializer()

			continue

		self.executor.run(existing_only=True)

		self.branches_changed.clear()

		return revision

	def print_progress_line(self, rev=None):

		if rev is None:
			if self.commits_made == self.prev_commits_made and self.commits_to_make == self.prev_commits_to_make:
				return

			self.print_progress_message("Processed %d revisions, made %d commits%s"
				% (self.total_revisions, self.commits_made, '' if self.commits_to_make == self.commits_made
					else (", %d pending        " % (self.commits_to_make - self.commits_made))), end='\r')
		elif self.commits_to_make:
			if rev == self.last_rev and self.commits_made == self.prev_commits_made and self.commits_to_make == self.prev_commits_to_make:
				return

			self.print_progress_message("Processing revision %s, total %d commits%s"
				% (rev, self.commits_made, '                      ' if self.commits_to_make == self.commits_made
					else (", %d pending        " % (self.commits_to_make - self.commits_made))), end='\r')
			self.last_rev = rev
		else:
			return super().print_progress_line(rev)

		self.prev_commits_made = self.commits_made
		self.prev_commits_to_make = self.commits_to_make
		return

	def print_last_progress_line(self):
		if not self.commits_made and not self.commits_to_make:
			super().print_last_progress_line()
		return

	def print_final_progress_line(self):
		if self.commits_made:
			self.print_progress_message("Processed %d revisions, made %d commits, written %d branches and %d tags in %s"
								% (self.total_revisions, self.commits_made, self.total_branches_made, self.total_tags_made, self.elapsed_time_str()))
		return

	def load(self, revision_reader):
		git_repo = self.git_repo

		self.branches_changed = []
		self.log_dump = False
		self.log_dump_all = False
		self.log_revs = False

		# Check if we can create a branch for the root directory
		branch_map = self.get_branch_map('/')
		if branch_map:
			self.add_branch(branch_map)

		if not git_repo:
			return super().load(revision_reader)

		self.log_serializer = self.make_log_serializer(executor=self.executor)
		self.log_file = self.log_serializer

		self.log_dump = getattr(self.options, 'log_dump', True)
		self.log_dump_all = getattr(self.options, 'log_dump_all', False)
		self.log_revs = getattr(self.options, 'log_revs', False)
		if self.options:
			self.options.log_dump = False
			self.options.log_dump_all = False
			self.options.log_revs = False

		# delete it if it existed
		shutil.rmtree(self.git_working_directory, ignore_errors=True)
		# make temp directory
		self.git_working_directory.mkdir(parents=True, exist_ok = True)

		try:
			super().load(revision_reader)

			for branch in self.all_branches():
				branch.ready()

			self.next_log_serializer()
			self.log_serializer.ready()
			self.executor.add_dependency(self.log_serializer)

			# Do blocked commits
			self.executor.ready()
			while self.executor.run(existing_only=True,block=True):
				self.update_progress(None)

			# Restore original log file
			self.log_file = self.options.log_file
			# Flush the log of revision ref updates
			self.log_file.write(self.revision_ref_log_file.getvalue())

			self.finalize_branches()

			# Flush leftover workitems (typically, only heads of deleted branches)
			while self.executor.run(existing_only=False,block=False): pass

			for ref, sha1 in self.prune_refs.items():
				self.total_refs_to_update += 1
				print('PRUNE REF: %s %s' % (sha1, ref), file=self.log_file)
				git_repo.queue_delete_ref(ref)

			self.print_progress_message(
				"\r                                                                  \r" +
				"Updating %d refs...." % self.total_refs_to_update, end='')

			git_repo.commit_refs_update()

			self.print_progress_message("done")
			self.print_final_progress_line()

			if self.options.sha1_map:
				self.save_sha1_map(self.options.sha1_map)

		finally:
			async_workitem.shutdown()
			self.shutdown()

		return

	def finalize_branches(self):
		# Gather all merged revisions
		all_merged_revisions = {}
		for branch in self.branches_list:	# branches_list has branches in order of creation
			branch.HEAD.export_merged_revisions(all_merged_revisions)

		for branch in self.branches_list:
			if branch.delete_if_merged:
				merged_at_rev = branch.HEAD.get_revision_merged_at(all_merged_revisions)
				if merged_at_rev is not None:
					if merged_at_rev.branch is branch:
						# The branch is empty
						print('Branch on SVN path "%s;%d" deleted because it doesn\'t have changess'
							% (branch.path, merged_at_rev.rev), file=self.log_file)
					else:
						# Deleting this branch because it's been merged
						print('Deleting the branch on SVN path "%s" because it has been merged to SVN path "%s" at rev %d'
							% (branch.path, merged_at_rev.branch.path, merged_at_rev.rev),
								file=self.log_file)
					continue

			# branch.finalize() writes the refs
			branch.finalize(all_merged_revisions)

		# Process remaining deleted revisions
		# Find which deleted revisions are not accessible from any other deleted revision
		remaining_deleted_revisions = []
		all_merged_deleted_revisions = {}
		for rev_info in self.deleted_revs:
			merged_at_rev = rev_info.prev_rev.get_revision_merged_at(all_merged_revisions)
			if merged_at_rev is not None:
				if merged_at_rev.branch is rev_info.branch:
					# Silently delete the revision because the branch is either empty
					# or merged back to same branch
					continue
				print('Deleted revision %s on SVN path "%s" has been merged to SVN path "%s" at rev %s'
					% (rev_info.rev, rev_info.branch.path, merged_at_rev.branch.path, merged_at_rev.rev),
					file=self.log_file)
				continue
			rev_info.prev_rev.export_merged_revisions(all_merged_deleted_revisions)
			remaining_deleted_revisions.append(rev_info)

		for rev_info in remaining_deleted_revisions:
			merged_at_rev = rev_info.prev_rev.get_revision_merged_at(all_merged_deleted_revisions)
			if merged_at_rev is not None:
				if merged_at_rev.branch is rev_info.branch:
					# Silently delete the revision because the deleted revision is either empty
					# or merged back to same branch
					continue
				print('Deleted revision %s on SVN path "%s" has been merged to SVN path "%s" at rev %s'
					% (rev_info.rev, rev_info.branch.path, merged_at_rev.branch.path, merged_at_rev.rev),
					file=self.log_file)
				continue

			rev_info.branch.finalize_deleted(rev_info.rev,
							rev_info.prev_rev.commit,
							rev_info.get_combined_revision_props(empty_message_ok=True))
			continue

		for (ref, info) in self.append_to_refs.items():
			print('APPEND REF(%s): %s %s'
				% (info.refs_root, info.sha1, ref), file=self.log_file)
			# FIXME: Check if the ref conflicts with existing
			self.total_refs_to_update += 1

			self.git_repo.queue_update_ref(ref, info.sha1)

		return

	def load_sha1_map(self, filename):
		try:
			with open(filename, 'rt', encoding='utf-8') as fd:
				for line in fd:
					obj_sha1, _, git_sha1 = line.strip().partition(' ')
					if obj_sha1 and git_sha1:
						self.prev_sha1_map[obj_sha1] = git_sha1
		except FileNotFoundError as fnf:
			pass
		return

	def save_sha1_map(self, filename):

		with open(filename, 'wt', encoding='utf-8') as fd:
			for obj_sha1, git_sha1 in sorted(self.sha1_map.items()):
				print(obj_sha1, git_sha1, file=fd)
		return

	def print_unmapped_directories(self, fd):
		unmapped = self.get_unmapped_directories()

		if unmapped:
			print("Unmapped directories:", file=fd)
			for dir in unmapped:
				print(dir, file=fd)

		return

	def load_authors_map(self, filename):
		with open(filename, 'rt', encoding='utf-8') as fd:
			authors_map = json.load(fd)

		for key, d in authors_map.items():
			name = d.get("Name")
			email = d.get("Email")
			if name and email:
				self.authors_map[key] = author_props(name, email)
		return

	def map_author(self, author):
		author_info = self.authors_map.get(author, None)
		if author_info is not None:
			return author_info

		self.unmapped_authors.append(author)

		author_info = author_props(author, author + "@localhost")

		self.authors_map[author] = author_info
		return author_info

	def print_unmapped_authors(self, fd):
		if len(self.unmapped_authors):
			print("Unmapped usernames:", file=fd)
			for name in sorted(self.unmapped_authors):
				print(name, file=fd)
		return

	def make_authors_file(self, filename):
		authors = {}
		for name in sorted(self.authors_map):
			author_info = self.authors_map[name]
			d = {
				"Name" : author_info.author,
				"Email" : author_info.email,
				}
			authors[name] = d

		with open(filename, 'wt', encoding='utf=8') as fd:
			json.dump(authors, fd, ensure_ascii=False, indent='\t')
		return

	def load_prev_refs(self, refs_roots, refs_list):
		refs_root_list = []
		for refs_root in refs_roots:
			if not refs_root.endswith('/'):
				refs_root += '/'
			if not refs_root.startswith('refs/'):
				refs_root = 'refs/' + refs_root
			refs_root_list.append(refs_root)

		for line in self.git_repo.for_each_ref('--format=%(objecttype) %(objectname) %(*objectname) %(*objecttype) %(*tree)%(tree) %(refname)', *refs_root_list):
			(objecttype, sha1, commit, rest) = line.split(None, 3)
			if objecttype == 'tag':
				# split wil produce type, tag sha1, commit sha1 (as sha2), rest of the line will have tree
				split = rest.split(None, 2)
				if len(split) != 3 or split[0] != 'commit':
					continue
				(type2, tree, ref) = split
			elif objecttype == 'commit':
				# split wil produce type, commit sha1, tree sha1 (as sha2), rest of the line will be ref
				tree = commit
				commit = sha1
				ref = rest
			else:
				continue

			for refs_root in refs_root_list:
				if not ref.startswith(refs_root):
					continue

				refname = ref.replace(refs_root, 'refs/', 1)
				if refs_list:
					# Filter the refs to append by the prune list
					for ref_match in refs_list:
						if ref_match.match(refname):
							break
					else:
						continue

				info = SimpleNamespace(type=objecttype, sha1=sha1, commit=commit, tree=tree, refs_root=refs_root)

				self.append_to_refs[refname] = info

				if re.fullmatch('refs/.*@r\d+', refname):
					self.all_refs.set(refname, [sha1])
				break
			continue
		return

	def load_refs_to_prune(self, refs_list):
		for ref_str in self.git_repo.for_each_ref('--format=%(objectname) %(refname)'):
			sha1, ref = ref_str.split(' ',1)
			for ref_match in refs_list:
				if ref_match.match(ref):
					self.prune_refs[ref] = sha1
					break

		return

def print_stats(fd):
	if TOTAL_FILES_REFORMATTED:
		print("Reformatting: done %d times, %d MiB" % (
			TOTAL_FILES_REFORMATTED, TOTAL_BYTES_IN_FILES_REFORMATTED//0x100000), file=fd)
	git_repo.print_stats(fd)
	return
