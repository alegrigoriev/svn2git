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

from history_reader import *
from lookup_tree import *
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
		self.tree = None
		# Next commit in history
		self.next_rev = None
		self.prev_rev = prev_rev

		self.props_list = []
		return

	def set_revision(self, revision):
		self.tree = revision.tree.find_path(self.branch.path)
		if self.tree is None:
			return None

		self.rev = revision.rev
		self.add_revision_props(revision)

		return self

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

		props_list.insert(0,
				revision_props(revision, log_to_paragraphs(log), author_info, date))
		return

## project_branch - keeps a context for a single change branch (or tag) of a project
class project_branch:

	def __init__(self, proj_tree:project_history_tree, branch_map):
		self.path = branch_map.path
		self.proj_tree = proj_tree
		# Matching project's config
		self.cfg:project_config.project_config = branch_map.cfg

		self.revisions = []
		self.first_revision = None

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
		self.HEAD = HEAD
		self.stage = project_branch_rev(self, HEAD)
		return

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

class project_history_tree(history_reader):

	def __init__(self, options=None):
		super().__init__(options)

		self.options = options
		self.log_file = options.log_file
		# This is a tree of branches
		self.branches = path_tree()
		# class path_tree iterates in the tree recursion order: from root to branches
		# branches_list will iterate in order in which the branches are created
		self.branches_list = []
		# This path tree is used to detect refname collisions, when a new branch
		# is created with an already existing ref
		self.all_refs = path_tree()
		# This is list of project configurations in order of their declaration
		self.project_cfgs_list = project_config.project_config.make_config_list(options.config,
											project_config.project_config.make_default_config(options))

		path_filter = getattr(options, 'path_filter', [])
		if path_filter:
			self.path_filters = [project_config.path_list_match(*path_filter,
											match_dirs=True, split=',')]
		else:
			# Make path filters from projects
			self.path_filters = [cfg.paths for cfg in self.project_cfgs_list]

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

	## Finds a base branch for the new path and current revision
	# @param path - the path to find a branch.
	#  The target branch path will be a prefix of 'path'
	def find_branch(self, path, match_full_path=False):
		return self.branches.find_path(path, match_full_path)

	def all_branches(self) -> Iterator[project_branch]:
		return (node.object for node in self.branches if node.object is not None)

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

		branch = project_branch(self, branch_map)
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

		self.all_refs.set(new_ref, new_ref)
		self.all_refs.set_used_by(new_ref, new_ref, path, match_full_path=True)
		return new_ref

	def apply_dir_node(self, node, base_tree):

		base_tree = super().apply_dir_node(node, base_tree)

		if node.action == b'add':
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

				continue

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

		return base_tree

	def load(self, revision_reader):

		# Check if we can create a branch for the root directory
		branch_map = self.get_branch_map('/')
		if branch_map:
			self.add_branch(branch_map)

		super().load(revision_reader)

		return
