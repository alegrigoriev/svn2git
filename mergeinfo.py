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

from rev_ranges import *

def parse_mergeinfo_line(line):
	# The line format:
	# path:[rev1-]rev2[,...]
	s = line.partition(':')
	if len(s) != 3:
		return (None, ranges)
	path = s[0]

	ranges = mergeinfo_str_to_ranges(s[2])

	# Older SVN versions set path in svn:mergeinfo without a leading slash
	if not path.startswith('/'):
		# Always return a leading slash
		path = '/' + path
	return (path, ranges)

def parse_svn_mergeinfo(svn_mergeinfo, dictionary):
	was_changed = False
	for line in svn_mergeinfo.splitlines():
		(path, revs) = parse_mergeinfo_line(line)
		if path is None:
			continue

		prev_ranges = dictionary.get(path, None)
		if prev_ranges is None:
			was_changed = True
			dictionary[path] = revs
			continue
		new_ranges = combine_ranges(prev_ranges, revs)
		if new_ranges != prev_ranges:
			dictionary[path] = new_ranges
			was_changed = True
		continue

	return was_changed

def replace_mergeinfo_suffix(dictionary, prev_path_suffix, new_path_suffix):
	# Mergeinfo paths never end with '/', even for directories.
	# Make sure the path being stripped or added (if non-empty) begins with '/' and doesn't end with '/'
	assert(not prev_path_suffix or (prev_path_suffix.startswith('/') and not prev_path_suffix.endswith('/')))
	assert(not new_path_suffix or (new_path_suffix.startswith('/') and not new_path_suffix.endswith('/')))
	if prev_path_suffix == new_path_suffix or len(dictionary) == 0:
		return dictionary

	new_dictionary = {}
	for path, ranges in dictionary.items():
		if path.endswith(prev_path_suffix):
			path = path.removesuffix(prev_path_suffix) + new_path_suffix
		new_dictionary[path] = ranges
	return new_dictionary

### This finds mergeinfo in a tree, looking to parents if not found on target
def find_mergeinfo(root_tree, path, skip_first=False):

	while True:
		path_split = path.rpartition('/')
		if not path_split[2] and path_split[0]:
			# The source path was not just '/', but was ending with '/'; discard the slash
			pass
		elif skip_first:
			skip_first = False
		else:
			dir_obj = root_tree.find_path(path)
			if dir_obj and dir_obj.svn_mergeinfo:
				return dir_obj.svn_mergeinfo, path

		path = path_split[0]
		if not path:
			break
		continue

	return "", ""

class mergeinfo:
	def __init__(self, svn_mergeinfo=""):
		self.paths_dict = {}
		# Mergeinfo is normalized when the merged ranges for child directories and
		# files don't coincide with ranges for their parent directories
		self.normalized = True
		self.add_mergeinfo_str(svn_mergeinfo)
		return

	def __len__(self):
		return len(self.paths_dict)

	def __eq__(self, other):
		if other is self:
			return True

		if type(other) is not type(self):
			return False
		return self.paths_dict == other.paths_dict

	def items(self):
		return self.paths_dict.items()

	def copy(self):
		c = type(self)()
		c.paths_dict = self.paths_dict.copy()
		c.normalized = self.normalized
		return c

	def add_mergeinfo_str(self, svn_mergeinfo):
		if not parse_svn_mergeinfo(svn_mergeinfo, self.paths_dict):
			return False
		# The mergeinfo dictionary changed, it may become non-normalized
		self.normalized = False
		return True

	def add_mergeinfo(self, add):
		was_changed = False
		for path, ranges in add.paths_dict.items():
			prev_ranges = self.paths_dict.get(path, None)
			if prev_ranges is None:
				was_changed = True
				self.paths_dict[path] = ranges
				continue
			new_ranges = combine_ranges(prev_ranges, ranges)
			if new_ranges != prev_ranges:
				self.paths_dict[path] = new_ranges
				was_changed = True
				continue
		if was_changed:
			self.normalized = False
		return was_changed

	# This function clear extra mergeinfo for child items, such as:
	# /dir:100-200
	# /dir/file:100-200
	# The second item will be removed, because it's covered by the first item
	def normalize(self, log_file=None):
		if self.normalized:
			return

		mergeinfo_list = [*self.paths_dict.items()]
		mergeinfo_list.sort()
		# In the sorted list, parent directories will come before shild directories
		self.paths_dict.clear()
		for path, ranges in mergeinfo_list:
			parent_path = path
			# Path never ends in '/'
			# Path always starts with '/' (root path is saved as '/')
			while parent_path:
				path_partitions = parent_path.rpartition('/')
				parent_path = path_partitions[0]
				prev_ranges = self.paths_dict.get(parent_path if parent_path else '/')
				if prev_ranges:
					new_ranges = subtract_ranges(ranges, prev_ranges)
					if log_file is not None and new_ranges != ranges:
						print("mergeinfo.normalize:\n"
							  "       Ranges for %s: %s" % (path, ranges_to_str(ranges)), file=log_file)
						print("       Subtract ranges for %s: %s" % (parent_path, ranges_to_str(prev_ranges)), file=log_file)
						print("       Remainder: %s" % (ranges_to_str(new_ranges)), file=log_file)
					ranges = new_ranges
					if not ranges:
						break
				continue
			if ranges:
				self.paths_dict[path] = ranges
			continue

		self.normalized = True
		return

	def replace_suffix(self, prev_path_suffix, new_path_suffix):
		if prev_path_suffix == new_path_suffix:
			# No change
			return False
		self.paths_dict = replace_mergeinfo_suffix(self.paths_dict, prev_path_suffix, new_path_suffix)
		self.normalized = False
		return True

	def find_path_mergeinfo(self, root_tree, path, skip_first=False):
		mergeinfo_str, found_path = find_mergeinfo(root_tree, path, skip_first)
		if not mergeinfo_str:
			return None
		# source 'path' may end with a slash. It always ends with a slash for a directory
		# source 'path' never starts with with a slash, except for a root directory '/'
		# found_path never ends with a slash, even for a directory
		# found_path is always a subdirectory of 'path'

		# new_path_suffix here is either empty, or starts with '/'
		assert(len(path) <= 1 or path[0] != '/')
		new_path_suffix = path.removesuffix('/').removeprefix(found_path)
		self.paths_dict.clear()
		if self.add_mergeinfo_str(mergeinfo_str):
			self.replace_suffix("", new_path_suffix)
		return found_path

	def get(self, path : str):
		path = path.removesuffix('/')
		if not path.startswith('/'):
			path = '/' + path

		return self.paths_dict.get(path, [])

	### Return difference in mergeinfo revisions as a new mergeinfo object
	def get_diff(self, prev_mergeinfo):
		diff_mergeinfo = mergeinfo()

		for path, ranges in self.paths_dict.items():
			parent_path = path
			# Path never ends in '/'
			# Path always starts with '/' (root path is saved as '/')
			my_ranges = ranges
			while ranges:
				prev_ranges = prev_mergeinfo.paths_dict.get(parent_path if parent_path else '/')
				if prev_ranges:
					if prev_ranges == ranges:
						ranges = None
						break
					ranges = subtract_ranges(ranges, prev_ranges)

				if not parent_path:
					break
				parent_path = parent_path.rpartition('/')[0]
				continue
			if ranges:
				if ranges is my_ranges:
					ranges = ranges.copy()
				diff_mergeinfo.paths_dict[path] = ranges
				diff_mergeinfo.normalized = self.normalized

		return diff_mergeinfo

	def get_diff_str(self, prev_mergeinfo):
		return str(self.get_diff(prev_mergeinfo))

	def __str__(self, prefix=''):
		# The keys are not in sorted order!
		items = list(self.paths_dict.items())
		items.sort()
		return ('\n' + prefix).join(("%s:%s" % (path, ranges_to_str(ranges))) for path, ranges in items)

class tree_mergeinfo():
	def __init__(self):
		# This is a dictionary with svn:mergeinfo strings as items, key is path in a tree
		# Path "" refers to the root directory.
		# If it's not present, path ".." may be present, which refers to the inherited mergeinfo.
		self.paths_dict = {}
		return

	def __len__(self):
		return len(self.paths_dict)

	def __str__(self):
		items = list(self.paths_dict.items())
		items.sort()
		return '\n'.join("For path %s:\n\t%s" % (path, path_mergeinfo.__str__(prefix='\t')) for path, path_mergeinfo in items)

	def copy(self):
		c = type(self)()
		c.paths_dict = { path : src_mergeinfo.copy() for path, src_mergeinfo in self.paths_dict.items() }
		return c

	def get(self, path):
		return self.paths_dict.get(path, None)

	def set_mergeinfo(self, path, src_mergeinfo):
		assert(type(src_mergeinfo) is mergeinfo)
		if len(src_mergeinfo) == 0:
			self.paths_dict.pop(path, None)
			return

		self.paths_dict[path] = src_mergeinfo
		if path == "":
			self.paths_dict.pop("..", None)
		return

	def add_mergeinfo(self, path, src_mergeinfo):
		assert(type(src_mergeinfo) is mergeinfo)
		prev_mergeinfo = self.paths_dict.get(path)
		if prev_mergeinfo is None:
			self.set_mergeinfo(path, src_mergeinfo.copy())
			return True
		return prev_mergeinfo.add_mergeinfo(src_mergeinfo)

	def set_mergeinfo_str(self, path, mergeinfo_str):
		if not mergeinfo_str:
			return self.paths_dict.pop(path, None) is not None

		self.set_mergeinfo(path, mergeinfo(mergeinfo_str))
		return

	def add_mergeinfo_str(self, path, mergeinfo_str):
		prev_mergeinfo = self.paths_dict.get(path)
		if prev_mergeinfo is None:
			return self.set_mergeinfo_str(path, mergeinfo_str)

		return prev_mergeinfo.add_mergeinfo_str(path, mergeinfo_str)

	### load_tree load this mergeinfo with mergeinfo from tree nodes starting from 'path'
	# If recurse_tree is True, the whole subtree is scanned, otherwise only the node
	# related by 'path'
	# If 'inherit', and the node at 'path' doesn't have mergeinfo,
	# parent directories also checked until svn_mergeinfo is found.
	# The inherited mergeinfo is loaded to the dictionary with key '..'
	def load_tree(self, root_tree, path, inherit=True, recurse_tree=False):
		# The source object must be empty
		assert(len(self.paths_dict) == 0)

		if root_tree is None:
			# throw exception?
			return self
		svn_mergeinfo = None

		root_obj = root_tree.find_path(path)
		if root_obj is not None:
			if recurse_tree:
				# combine all mergeinfo in the tree
				for (obj_path, obj) in root_obj:
					if obj_path == '':
						continue
					# paths don't have trailing slashes
					if obj.svn_mergeinfo is not None:
						if obj.is_dir():
							obj_path += '/'
						self.set_mergeinfo_str(obj_path, obj.svn_mergeinfo)

			svn_mergeinfo = root_obj.svn_mergeinfo
			if svn_mergeinfo:
				# Only one level of mergeinfo is processed
				self.set_mergeinfo_str('', svn_mergeinfo)

		if inherit and not svn_mergeinfo:
			svn_mergeinfo = mergeinfo()
			if svn_mergeinfo.find_path_mergeinfo(root_tree,path, skip_first=True):
				self.set_mergeinfo('..', svn_mergeinfo)

		return self

	### get_subtree_mergeinfo filters the tree_mergeinfo for the given subtree path, and adjust the resulting mergeinfo
	# If filter_path refers to a directory, it must be ending in a slash
	def get_subtree_mergeinfo(self, src_tree_mergeinfo, filter_path):
		# The source object must be empty
		assert(len(self.paths_dict) == 0)

		parent_mergeinfo = None
		parent_mergeinfo_path = ""
		for path, src_mergeinfo in src_tree_mergeinfo.paths_dict.items():
			if not path or path.endswith('/'):
				# Mergeinfo for a directory object
				if filter_path.endswith('/') and path.startswith(filter_path):
					self.paths_dict[path.removeprefix(filter_path)] = src_mergeinfo
				elif filter_path.startswith(path) \
						and (parent_mergeinfo is None \
						or filter_path.startswith(parent_mergeinfo_path)):
					parent_mergeinfo = src_mergeinfo
					parent_mergeinfo_path = path
			elif path == filter_path:
				# This is a mergeinfo for a file
				self.paths_dict[path.removeprefix(filter_path)] = src_mergeinfo

		if not parent_mergeinfo:
			parent_mergeinfo = src_tree_mergeinfo.paths_dict.get('..')

		if parent_mergeinfo:
			parent_mergeinfo_path = filter_path.removeprefix(parent_mergeinfo_path)
			if parent_mergeinfo_path == "":
				self.paths_dict[''] = parent_mergeinfo
			else:
				# Adjust the mergeinfo for subpath
				parent_mergeinfo = parent_mergeinfo.copy()
				parent_mergeinfo.replace_suffix('', '/' + parent_mergeinfo_path.removesuffix('/'))
				self.paths_dict['..'] = parent_mergeinfo
		return self

	def add_tree_mergeinfo(self, src_tree_mergeinfo, prev_path_prefix = "", new_path_prefix = ""):
		was_changed = False
		assert(type(src_tree_mergeinfo) is tree_mergeinfo)
		if prev_path_prefix.endswith('/'):
			# Directory
			if not (not new_path_prefix or new_path_prefix.endswith('/')):
				print(f'{prev_path_prefix=},{new_path_prefix=}', file=sys.stderr)
			assert(not new_path_prefix or new_path_prefix.endswith('/'))
		elif prev_path_prefix:
			# previous path prefix is a filename. New path prefix must also be a filename
			if not (new_path_prefix and not new_path_prefix.endswith('/')):
				print(f'{prev_path_prefix=},{new_path_prefix=}', file=sys.stderr)
			assert(new_path_prefix and not new_path_prefix.endswith('/'))

		for path, src_mergeinfo in src_tree_mergeinfo.paths_dict.items():
			assert(type(src_mergeinfo) is mergeinfo)
			if prev_path_prefix:
				if prev_path_prefix.endswith('/'):
					# Directory
					if not path.startswith(prev_path_prefix):
						path = '..'
					else:
						path = new_path_prefix + path.removeprefix(prev_path_prefix)
				elif path == prev_path_prefix:
					path = new_path_prefix
				else:
					path = '..'

			if self.add_mergeinfo(path, src_mergeinfo):
				was_changed = True
			continue
		return was_changed

	def build_mergeinfo(self, normalize=False, log_file=None):
		new_mergeinfo = mergeinfo()
		for self_mergeinfo in self.paths_dict.values():
			new_mergeinfo.add_mergeinfo(self_mergeinfo)
		if normalize:
			new_mergeinfo.normalize(log_file=log_file)
		return new_mergeinfo
