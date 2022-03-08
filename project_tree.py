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
import re
from pathlib import Path
import shutil
from types import SimpleNamespace
import git_repo

from history_reader import *
from lookup_tree import *
from mergeinfo import *
import project_config

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
class project_branch_rev:
	def __init__(self, branch:project_branch, prev_rev=None):
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
		# revisions_to_merge is a map of revisions pending to merge, keyed by branch.
		self.revisions_to_merge = None
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
			# merged_revisions is a map of merged revisions keyed by branch.
			# It either refers to the previous revision's map,
			# or a copy is made and modified
			# Its values are tuples (merged_revision, revision_merged_at)
			self.merged_revisions = prev_rev.merged_revisions
			# propagate previous mergeinfo and tree_mergeinfo
			self.mergeinfo = prev_rev.mergeinfo
			# tree_mergeinfo will be copied on modification.
			self.tree_mergeinfo = prev_rev.tree_mergeinfo

		# list of rev-info the commit on this revision would depend on - these are parent revs for the rev's commit
		self.parents = []
		self.merge_from_dict = None
		self.copy_sources = None
		self.cherry_pick_revs = None
		self.props_list = []
		return

	def set_revision(self, revision):
		self.tree = revision.tree.find_path(self.branch.path)
		if self.tree is None:
			return None

		self.rev = revision.rev
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

			if rev_info.commit not in cherry_pick_commits:
				cherry_pick_commits[rev_info.commit] = rev_info

		for rev_info in cherry_pick_commits.values():
			refname = re.sub('(?:^refs/(?:heads/)?)(.*)?', r'\1', rev_info.branch.refname)
			if not refname:
				refname = rev_info.branch.path

			merge_msg.append("Cherry-picked-from: %s %s;%d" % (rev_info.commit, refname, rev_info.rev))

		return '\n'.join(merge_msg)

	### The function returns a single revision_props object, with:
	# .log assigned a list of text paragraphs,
	# .author, date, email, revision assigned from most recent revision_props
	def get_combined_revision_props(self, base_rev=None, empty_message_ok=False, decorate_revision_id=False):
		props_list = self.props_list
		if not props_list:
			return None

		prop0 = props_list[0]
		msg = prop0.log.copy()

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
			author_info = author_props(revision.author, revision.author + "@localhost")
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

				if recreate_merge:
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

		if self.revisions_to_merge is None:
			self.revisions_to_merge = {}
		else:
			# Check if this revision or its descendant has been added for merge already
			merged_rev = self.revisions_to_merge.get(add_rev.branch)
			if merged_rev is not None and merged_rev.rev >= add_rev.rev:
				return

		self.revisions_to_merge[add_rev.branch] = add_rev

		# Now add previously merged revisions from add_rev to the merged_revisions dictionary
		for (rev_info, merged_on_rev) in add_rev.merged_revisions.values():
			if not self.is_merged_from(rev_info):
				self.set_merged_revision(rev_info, merged_on_rev)
			continue
		return

	def process_parent_revisions(self, HEAD):
		# Either tree is known, or previous commit was imported from previous refs
		if HEAD.tree:
			self.parents.append(HEAD)

		self.process_merge_delta()	# Can add more merged revisions

		# Process revisions to merge dictionary, if present
		if self.revisions_to_merge is not None:
			for parent_rev in self.revisions_to_merge.values():
				# Add newly merged revisions to self.merged_revisions dict
				if self.is_merged_from(parent_rev):
					continue

				self.set_merged_revision(parent_rev)

				self.parents.append(parent_rev)
				continue

			self.revisions_to_merge = None

		return

	### Get which revision of the branch of interest have been merged
	def get_merged_revision(self, rev_info_or_branch):
		if type(rev_info_or_branch) is project_branch_rev:
			rev_info_or_branch = rev_info_or_branch.branch

		(merged_rev, merged_at_rev) = self.merged_revisions.get(rev_info_or_branch, (None,None))
		return merged_rev

	def set_merged_revision(self, merged_rev, merged_at_rev=None):
		if merged_at_rev is None:
			merged_at_rev = self

		if self.merged_revisions is self.prev_rev.merged_revisions:
			self.merged_revisions = self.prev_rev.merged_revisions.copy()
		self.merged_revisions[merged_rev.branch] = (merged_rev, merged_at_rev)
		return

	### Get at which revision of the branch or revision of interest have been merged
	# The revision of interest might have gotten merged into one of ancestor branches.
	# If traverse_ancestor_branches is True, find to which revision of the current branch
	# they got ultimately merged.
	def get_merged_at_revision(self, rev_info_or_branch, traverse_ancestor_branches=False):
		if type(rev_info_or_branch) is project_branch_rev:
			rev_info_or_branch = rev_info_or_branch.branch

		while True:
			(merged_rev, merged_at_rev) = self.merged_revisions.get(rev_info_or_branch, (None,None))
			if merged_at_rev is None:
				break
			if merged_at_rev.branch is self.branch:
				break
			if not traverse_ancestor_branches:
				break
			rev_info_or_branch = merged_at_rev.branch
			continue
		return merged_at_rev

	### Find least range of revisions starting with rev_to_merge
	# not sharing common ancestors with self
	def find_unmerged_ranges(self, rev_to_merge):
		# The merge base is the most recent commit of this branch
		# or its ancestors shared with
		unmerged_ranges = []
		for (rev_info, merged_at) in ((rev_to_merge, rev_to_merge.get_merged_at_revision(rev_to_merge)), \
				*self.merged_revisions.values()):
			rev_info = rev_info.walk_back_empty_revs()
			if rev_info.prev_rev.rev is None:
				continue
			merged_at2 = self.get_merged_at_revision(rev_info, traverse_ancestor_branches=True)
			if merged_at2 is not None:
				merged_at2 = merged_at2.walk_back_empty_revs()
				if rev_info.rev <= merged_at2.rev:
					continue
			unmerged_ranges.append((merged_at2, rev_info))
			continue
		return unmerged_ranges

	### Returns True if rev_info_or_branch (if branch, then its HEAD) is one of the ancestors of 'self'.
	# If rev_info_or_branch is a branch, its HEAD is used.
	# If skip_empty_revs is True, then the revision of interest is considered merged
	# even if it's a descendant of the merged revision, but there's been no changes
	# between them
	def is_merged_from(self, rev_info_or_branch, skip_empty_revs=False):
		if type(rev_info_or_branch) is project_branch:
			branch = rev_info_or_branch
			rev_info = branch.HEAD
		else:
			branch = rev_info_or_branch.branch
			rev_info = rev_info_or_branch

		if branch is self.branch:
			# A previous revision of the same branch
			# is considered merged
			return True

		merged_rev = self.get_merged_revision(branch)
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

	def get_difflist(self, old_tree, new_tree):
		branch = self.branch
		if old_tree is None:
			old_tree = branch.proj_tree.empty_tree
		if new_tree is None:
			new_tree = branch.proj_tree.empty_tree

		difflist = []
		for t in old_tree.compare(new_tree, "", expand_dir_contents=True):
			path = t[0]
			obj1 = t[1]
			obj2 = t[2]
			item1 = t[3]
			item2 = t[4]

			self.process_svn_mergeinfo(path, obj1, obj2)

			difflist.append(t)
			continue

		return difflist

	def build_difflist(self, HEAD):

		return self.get_difflist(HEAD.tree, self.tree)

	def get_stagelist(self, difflist, stagelist):
		branch = self.branch

		for t in difflist:
			path = t[0]
			obj1 = t[1]
			obj2 = t[2]
			item1 = t[3]
			item2 = t[4]

			if obj2 is None:
				# a path is deleted
				if not obj1.is_file():
					continue

				stagelist.append(SimpleNamespace(path=path, obj=None, mode=0))
				continue

			if not obj2.is_file():
				continue

			if item2 is not None and hasattr(item2, 'mode'):
				mode = item2.mode
			else:
				mode = branch.get_file_mode(path, obj2)

			stagelist.append(SimpleNamespace(path=path, obj=obj2, mode=mode))
			continue

		return

	def build_stagelist(self, HEAD):
		HEAD = self.get_staging_base(HEAD)

		difflist = self.build_difflist(HEAD)
		# Parent revs need to be processed before building the stagelist
		self.process_parent_revisions(HEAD)

		branch = self.branch

		stagelist = []
		self.get_stagelist(difflist, stagelist)

		self.git_env = branch.git_env

		for item in stagelist:
			obj = item.obj
			if obj is None:
				continue
			if obj.git_sha1 is not None:
				continue

			if obj.is_symlink():
				path = None
				data = obj.data[5:]
			else:
				path = item.path
				data = obj.data

			obj.git_sha1 = branch.hash_object(data,
								path, self.git_env)
			continue

		self.staged_tree = self.tree
		self.any_changes_present = len(stagelist) != 0

		return stagelist

	def apply_stagelist(self, stagelist):
		branch = self.branch
		git_repo = branch.git_repo
		git_env = self.git_env

		if self.staging_base_rev is not self.prev_rev:
			# to stage this commit, we need to read the specific base tree into index. Usually it's the first parent.
			git_repo.read_tree(self.staging_base_rev.staged_git_tree, '-i', '--reset', env=git_env)

		if stagelist:
			branch.stage_changes(stagelist, git_env)
			return git_repo.write_tree(git_env)
		else:
			return self.staging_base_rev.staged_git_tree

## project_branch - keeps a context for a single change branch (or tag) of a project
class project_branch:

	def __init__(self, proj_tree:project_history_tree, branch_map, workdir:Path):
		self.path = branch_map.path
		self.proj_tree = proj_tree
		# Matching project's config
		self.cfg:project_config.project_config = branch_map.cfg
		self.git_repo = proj_tree.git_repo

		self.inherit_mergeinfo = branch_map.inherit_mergeinfo
		self.delete_if_merged = branch_map.delete_if_merged
		self.recreate_merges = branch_map.recreate_merges

		self.revisions = []
		self.first_revision = None

		self.edit_msg_list = []
		for edit_msg in *branch_map.edit_msg_list, *self.cfg.edit_msg_list:
			if edit_msg.branch.fullmatch(self.path):
				self.edit_msg_list.append(edit_msg)
			continue

		# Absolute path to the working directory.
		# index file (".git.index") will be placed there
		self.git_index_directory = workdir
		if workdir:
			workdir.mkdir(parents=True, exist_ok = True)

		self.git_env = self.make_git_env()

		# Null tree SHA1
		self.initial_git_tree = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'

		# Full ref name for Git branch or tag for this branch
		self.refname = branch_map.refname
		self.alt_refname = branch_map.alt_refname

		if branch_map.revisions_ref:
			self.revisions_ref = branch_map.revisions_ref
		elif self.refname.startswith('refs/heads/'):
			self.revisions_ref = branch_map.refname.replace('refs/heads/', 'refs/revisions/', 1)
		else:
			self.revisions_ref = branch_map.refname.replace('refs/', 'refs/revisions/', 1)

		self.init_head_rev()

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
			return self.git_repo.make_env(
					work_dir=str(self.git_index_directory),
					index_file=str(self.git_index_directory.joinpath(".git.index")))
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
	def make_commit(self, revision):
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
			return

		stagelist = rev_info.build_stagelist(HEAD)

		rev_info.staged_git_tree = rev_info.apply_stagelist(stagelist)

		# Can only make the next stage rev after done with building the stagelist
		# and processing the parent revision
		self.stage = project_branch_rev(self, rev_info)

		parent_commits = []
		parent_git_tree = self.initial_git_tree
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

		base_rev = None
		for parent_rev in rev_info.parents:
			if parent_rev.commit is None:
				continue
			if parent_rev.commit not in parent_commits:
				parent_commits.append(parent_rev.commit)
				if base_rev is None or base_rev.committed_git_tree == self.initial_git_tree:
					base_rev = parent_rev

		if base_rev is not None:
			parent_git_tree = base_rev.committed_git_tree
			parent_tree = base_rev.committed_tree
			commit = base_rev.commit

		need_commit = rev_info.staged_git_tree != parent_git_tree
		if len(parent_commits) > 1:
			need_commit = True

		if need_commit:
			rev_props = rev_info.get_commit_revision_props(base_rev)
			author_info = rev_props.author_info

			commit = git_repo.commit_tree(rev_info.staged_git_tree, parent_commits, rev_props.log,
					author_name=author_info.author, author_email=author_info.email, author_date=rev_props.date,
					committer_name=author_info.author, committer_email=author_info.email, committer_date=rev_props.date,
					env=self.git_env)

			print("COMMIT:%s REF:%s PATH:%s;%s" % (commit, self.refname, self.path, rev_info.rev), file=rev_info.log_file)
			if self.proj_tree.log_commits:
				print(self.git_repo.show(commit, "--raw", "--parents", "--no-decorate", "--abbrev-commit"), file=rev_info.log_file)

			# Make a ref for this revision in refs/revisions namespace
			if self.revisions_ref:
				self.update_ref('%s/r%s' % (self.revisions_ref, rev_info.rev), commit, log_file=self.proj_tree.revision_ref_log_file)

			rev_info.rev_commit = commit	# commit made on this revision, not inherited
			rev_info.committed_git_tree = rev_info.staged_git_tree
			rev_info.committed_tree = rev_info.tree
			self.proj_tree.commits_made += 1
		else:
			rev_info.committed_git_tree = parent_git_tree
			rev_info.committed_tree = parent_tree

		rev_info.commit = commit
		return

	def stage_changes(self, stagelist, git_env):
		git_process = self.git_repo.update_index(git_env)
		pipe = git_process.stdin
		for item in stagelist:
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

		if obj.svn_executable is not None:
			return 0o100755
		return 0o100644

	def hash_object(self, data, path, git_env):
		return self.git_repo.hash_object_async(data, path, env=git_env)

	def preprocess_blob_object(self, obj, node_path):
		proj_tree = self.proj_tree
		# Cut off the branch path to make relative paths
		path = node_path.removeprefix(self.path)

		if obj.is_symlink():
			return obj

		# Find git attributes - TODO fill cfg.gitattributes
		for attr in self.cfg.gitattributes:
			if attr.pattern.fullmatch(path) and obj.git_attributes.get(attr.key) != attr.value:
				obj = obj.make_unshared()
				obj.git_attributes[attr.key] = attr.value

		obj = proj_tree.finalize_object(obj)
		return obj

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
		return

	def get_git_sha1(self):
		return str(self.git_sha1)

	def is_symlink(self):
		return self.svn_special is not None \
			and self.data.startswith(b'link ')

class project_history_tree(history_reader):
	BLOB_TYPE = git_blob
	TREE_TYPE = git_tree

	def __init__(self, options=None):
		super().__init__(options)

		self.options = options
		self.log_file = options.log_file
		self.log_commits = getattr(options, 'log_commits', False)
		self.log_merges_verbose = getattr(options, 'log_merges_verbose', False)
		self.log_merges = self.log_merges_verbose or getattr(options, 'log_merges', False)

		# This is a tree of branches
		self.branches = path_tree()
		# class path_tree iterates in the tree recursion order: from root to branches
		# branches_list will iterate in order in which the branches are created
		self.branches_list = []
		# Memory file to write revision ref updates
		self.revision_ref_log_file = io.StringIO()
		# This path tree is used to detect refname collisions, when a new branch
		# is created with an already existing ref
		self.all_refs = path_tree()
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

		self.commits_made = 0
		self.branch_dir_index = 1	# Used for branch working directory
		self.total_branches_made = 0
		self.total_tags_made = 0
		self.total_refs_to_update = 0
		self.prev_commits_made = None

		return

	def shutdown(self):
		self.git_repo.shutdown()
		shutil.rmtree(self.git_working_directory, ignore_errors=True)
		self.git_working_directory = None
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
		return

	def get_branch_map(self, path):
		if not path.endswith('/'):
			# Make sure there's a slash at the end
			path += '/'

		mapped = self.branches.get_mapped(path, match_full_path=True)
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
			if self.branches.get_mapped(path, match_full_path=False) is None:
				print('Directory mapping: No map for "%s" to create a branch' % path, file=self.log_file)

		# Save the unmapped directory
		self.branches.set_mapped(path, False)
		return None

	## Adds a new branch for path in this revision, possibly with source revision
	# The function must not be called when a branch already exists
	def add_branch(self, branch_map):
		print('Directory "%s" mapping with globspec "%s" in config "%s":'
				% (branch_map.path, branch_map.globspec, branch_map.cfg.name),
				file=self.log_file)

		if self.git_working_directory:
			git_workdir = Path(self.git_working_directory, str(self.branch_dir_index))
			self.branch_dir_index += 1
		else:
			git_workdir = None

		branch = project_branch(self, branch_map, git_workdir)
		if branch.refname:
			print('    Added new branch %s' % (branch.refname), file=self.log_file)
		else:
			print('    Added new unnamed branch', file=self.log_file)

		self.branches.set(branch_map.path, branch)
		self.branches.set_mapped(branch_map.path, True)
		self.branches_list.append(branch)

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

		if ref.startswith('refs/tags/'):
			self.total_tags_made += 1
		elif ref.startswith('refs/heads/'):
			self.total_branches_made += 1

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

		return tagname

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
				# Check if we need and can create a branch for this directory
				branch_map = self.get_branch_map(root_path + path)
				if not branch_map:
					continue

				branch = self.find_branch(branch_map.path, match_full_path=True)
				if not branch:
					branch = self.add_branch(branch_map)
					if not branch:
						continue

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

				branch.add_copy_source(source_path, target_path, node.copyfrom_rev, None)
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

		if not self.filter_path(node.path, node.kind, base_tree):
			print("IGNORED: Node ignored because of --path-filter option", file=self.log_file)
			return base_tree

		# Check if the copy source refers to a path filtered out
		if node.copyfrom_path is not None and not self.filter_path(node.copyfrom_path, node.kind, base_tree) and node.text_content is None:
			raise Exception_history_parse('Node Path="%s": Node-copyfrom-path "%s" refers to a filtered-out directory'
						% (node.path, node.copyfrom_path))

		base_tree = super().apply_node(node, base_tree)

		branch = self.find_branch(node.path)
		if branch is None:
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
		if node.action != b'delete':
			branch = self.find_branch(node.path)
			if branch:
				file = base_tree.find_path(node.path)
				base_tree = base_tree.set(node.path, file,
								mode=branch.get_file_mode(node.path, file))
		return base_tree

	def apply_revision(self, revision):
		# Apply the revision to the previous revision, checking if new branches are created
		# into commit(s) in the git repository.

		revision = super().apply_revision(revision)

		# make commits
		for branch in self.branches_changed:
			branch.make_commit(revision)

		self.branches_changed.clear()

		return revision

	def print_progress_line(self, rev=None):

		if rev is None:
			if self.commits_made == self.prev_commits_made:
				return

			self.print_progress_message("Processed %d revisions, made %d commits"
				% (self.total_revisions, self.commits_made), end='\r')

		elif self.commits_made:
			if self.commits_made == self.prev_commits_made:
				return

			self.print_progress_message("Processing revision %s, total %d commits"
				% (rev, self.commits_made), end='\r')
		else:
			return super().print_progress_line(rev)

		self.prev_commits_made = self.commits_made
		return

	def print_last_progress_line(self):
		if not self.commits_made:
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

		# Check if we can create a branch for the root directory
		branch_map = self.get_branch_map('/')
		if branch_map:
			self.add_branch(branch_map)

		if not git_repo:
			return super().load(revision_reader)

		# delete it if it existed
		shutil.rmtree(self.git_working_directory, ignore_errors=True)
		# make temp directory
		self.git_working_directory.mkdir(parents=True, exist_ok = True)

		try:
			super().load(revision_reader)

			# Flush the log of revision ref updates
			self.log_file.write(self.revision_ref_log_file.getvalue())

			self.finalize_branches()

			self.print_progress_message(
				"\r                                                                  \r" +
				"Updating %d refs...." % self.total_refs_to_update, end='')

			git_repo.commit_refs_update()

			self.print_progress_message("done")
			self.print_final_progress_line()

		finally:
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

		return

def print_stats(fd):
	git_repo.print_stats(fd)
