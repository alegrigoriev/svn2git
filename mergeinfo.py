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
