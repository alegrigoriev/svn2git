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

### lookup_tree does tree-based lookup for arbitrary items.
# It can match either full path, or prefix of the given path
# Any numbed of extra slashes is ignored
# The input path can start with '/', it's considered the same as with no '/'.
# The path can end with '/', it's same as without '/'.
class lookup_tree:
	def __init__(self):
		self.dict = {}
		return

	# Find/add a node in the tree
	# When the full path matches a node, return it
	# If there's no full path match, and add_if_missing==True,
	# add missing path.
	# if not add_if_missing and match_full_path=False and path is longer,
	# return partial path
	# Recursion is replaced by a loop
	def get_node(self, path, match_full_path=False, add_if_missing=False):

		while True:
			if path == '':
				return self

			split = path.split('/', 1)

			if split[0] != '':
				t = self.dict.get(split[0])
				if t is None:
					if add_if_missing:
						# The path component not in dictionary.
						# path element with this name didn't exist
						# Duplicate type of the tree when creating another item
						t = type(self)()
						self.dict[split[0]] = t
					elif not match_full_path:
						return self
					else:
						return None

				self = t
				if len(split) == 1:
					return self

			path = split[1]
			continue

	# This iterator returns nodes of the tree
	def __iter__(self):
		class tree_iter:
			def __init__(self, tree):
				# This iterator returns (key, value) tuples of the dictionary items
				self.dict_iter = iter(tree.dict.items())
				self.child_iter = None
				self.node = tree
				return

			def __iter__(self):
				return self

			def __next__(self):
				if self.node is not None:
					t = self.node
					self.node = None
					return t
				while True:
					if self.child_iter is None:
						# t is (key, value) tuple
						t = next(self.dict_iter)
						if t[0].endswith('/'):
							continue
						self.child_iter = iter(t[1])
					try:
						return next(self.child_iter)
					except StopIteration:
						self.child_iter = None

		return tree_iter(self)

class path_tree(lookup_tree):
	def __init__(self):
		super().__init__()
		self.object = None
		self.used_by = {}
		self.mapped = None
		return

	# When the path matches a leaf element, return it,
	# unless match_full_path=True and path is longer
	def find_path(self, path, match_full_path=False):
		node = self.get_node(path, match_full_path=match_full_path)
		if node:
			return node.object
		else:
			return None

	# used_by string is set in the dictionary with '/' appended, to make sure
	# it doesn't collide with any path element
	# used_by marks every level of the path
	# If used_by string is given,
	# it's stored at every level of the tree except root
	def set(self, path : str, object, replace_ok=True):
		node = self.get_node(path, add_if_missing=True)

		prev = node.object
		if prev is None or replace_ok:
			node.object = object
		return prev

	def set_used_by(self, path, key, object, match_full_path=False):
		node = self.get_node(path, match_full_path=match_full_path)

		if node:
			node.used_by[key] = object
		return

	# if key==None, the very first (key, object) tuple is returned
	def get_used_by(self, path='', key=None, match_full_path=False):
		node = self.get_node(path, match_full_path=match_full_path)

		if node:
			if key:
				return node.used_by.get(key)
			elif node.used_by.items():
				# return the very first key+data tuple
				return next(iter(node.used_by.items()))
		return None

	# Mapped: False - the node is explicitly excluded from mapping
	# Mapped: True - the node mapped to a branch
	def get_mapped(self, path, match_full_path=True):
		node = self.get_node(path, match_full_path=match_full_path)

		if node:
			return node.mapped
		return None

	def set_mapped(self, path, mapped):
		node = self.get_node(path, add_if_missing=True)

		if node:
			node.mapped = mapped
		return

	def items(self, path=''):
		# The iterator returns tuples of (path, object) for the whole tree with subtrees
		if self.object is not None:
			yield (path, self.object)
		if path:
			path += '/'
		for key, node in self.dict.items():
			yield from node.items(path + key)
		return
