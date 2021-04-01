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

import sys
import time
import datetime
from svn_dump_reader import make_data_sha1
from exceptions import Exception_history_parse
import hashlib

def apply_delta_to_properties(props, delta):
	if props is None:
		props = {}
	else:
		props = props.copy()

	for (key, data) in delta.items():
		if data is None:
			if key not in props:
				raise Exception_history_parse("Delta properties: trying to delete non-present key '%s'" % key)
			props.pop(key)
		else:
			props[key] = data
	return props

class svn_object:
	def __init__(self, src = None, properties=None):
		# svn_sha1 is calculated in different way, depending on the object type. This is 'bytes' object.
		self.svn_sha1 = None
		# SVN properties of this file or directory
		if src:
			if properties is not None:
				self.properties = properties.copy()
			else:
				self.properties = src.properties.copy()
		elif properties:
			self.properties = properties.copy()
		else:
			self.properties = {}

		return

	### These functions are used to tell the object type: whether it's a file or directory
	def is_dir(self):
		return False
	def is_file(self):
		return False

	def make_unshared(self):
		if self.svn_sha1 is None:
			return self
		# To allow modification of the blob,
		# If the tree object has been hashed before,
		# we need to clone it, because hash will be invalidated
		return self.copy()

	### This function makes a new svn_tree as copy of self,
	# which includes list and dictionary of items, attributes dictionary, and hash values
	def copy(self):
		return type(self)(self)

	### This function replaces or edits properties of an object
	# if the properties didn't come as delta, the properties dictionary
	# gets just replaced with new properties
	def set_properties(self, props, is_delta=False):
		if not is_delta and self.properties == props:
			return self

		self = self.make_unshared()

		if is_delta:
			self.properties = apply_delta_to_properties(self.properties, props)
		else:
			self.properties = props.copy()

		return self

	def get_hash(self):
		return self.svn_sha1

	### finalize() assigns svn_sha1 to the object. SHA1 is calculated by calling make_svn_hash
	# The objects are placed to svn_object.dictionary map, keyed by their SVN SHA1 byte string.
	# If SHA1 is already present in the dictionary, the existing object is substituted,
	# such as there are never two finalized objects with the same hash
	# The function returns either the original object, or the existing
	# object from the map
	def finalize(self, dictionary):
		if self.is_finalized():
			return self

		self.svn_sha1 = self.make_svn_hash().digest()
		# check if such object is already present in the dictionary
		existing_obj = dictionary.get(self.svn_sha1)
		if existing_obj:
			return existing_obj

		dictionary[self.svn_sha1] = self
		return self

	# make_svn_hash() function calculates the full hash of complete svn_tree,
	# all its subelements, properties, and Git attributes
	def make_svn_hash(self, prefix=b'OBJECT\n'):
		h = hashlib.sha1()
		h.update(prefix)
		# The dictionary provides the list in order of adding items
		# Make sure the properties are hashed in sorted order.
		props = list(self.properties.items())
		props.sort()
		for (key, data) in props:
			h.update(b'PROP: %s %d\n' % (key, len(data)))
			h.update(data)

		return h

	def is_finalized(self):
		return self.svn_sha1 is not None

### svn_blob describes text contents from SVN,
# and also its file properties and attributes
# To avoid keeping copies of identical blobs, all files with
# identical SHA1 refer to the same data blob object,
#  which is also kept as svn_blob, but with empty attributes and properties
class svn_blob(svn_object):
	def __init__(self, src = None, properties=None):
		super().__init__(src, properties)
		if src:
			# data may not be present
			self.data = src.data
			# keep the length, because we may not be keeping the bytes of blob itself
			self.data_len = src.data_len
			# this is sha1 of data only, as 40 chars hex string.
			self.data_sha1 = src.data_sha1
		else:
			self.data = None
			self.data_len = 0
			self.data_sha1 = None
		return

	def is_file(self):
		return True

	def __str__(self, prefix=''):
		return prefix

	# return hashlib SHA1 object filled with hash of prefix, data SHA1, and SHA1 of all attributes
	def make_svn_hash(self):
		# svn_sha1 of a svn_blob object is calculated as sha1 of:
		# b'BLOB', then length as decimal string, terminated with '\n', then 20 bytes of data hash in binary form
		# This avoids running sha1 on data twice.
		# Also, it includes hashes of attribute key:value pairs of self.attributes dictionary
		return super().make_svn_hash(b'BLOB %d\n%s' % (len(self.data), self.data_sha1))

### This object describes a directory, similar to Git tree object
# It's identified by its specific SHA1, calculated over hashes of items, and also over its attributes
# Two trees with identical files but different SVN attributes will have different hash values
class svn_tree(svn_object):
	def __init__(self, src = None, properties=None):
		super().__init__(src, properties)
		# items are svn_tree.item instances
		if src:
			self.items = src.items.copy()
			self.dict = src.dict.copy()
		else:
			self.items = []
			self.dict = {}
		return

	class item:
		def __init__(self, name, obj=None):
			self.name = name
			self.object = obj
			return

	def __iter__(self, path=''):
		# The iterator returns tuples of (path, object) for the whole tree with subtrees
		yield path, self

		for name, item in self.dict.items():
			name = path + name
			obj = item.object
			if not obj.is_dir():
				yield name, obj
			else:
				yield from obj.__iter__(name + '/')
			continue
		return

	### These functions are used to tell the object type: whether it's a file or directory
	def is_dir(self):
		return True

	def finalize(self, dictionary):
		if not self.is_finalized():
			self.items.sort(key=lambda t : t.name)
			for item in self.items:
				item.object = item.object.finalize(dictionary)
		return super().finalize(dictionary)

	# make_svn_hash() function calculates the full hash of complete svn_tree,
	# all its subelements, properties, and Git attributes
	def make_svn_hash(self):
		h = super().make_svn_hash(b'TREE\n')

		# child object hashes are combined in sorted name order
		for item in self.items:
			h.update(b'ITEM: %s\n' % (item.name.encode(encoding='utf-8')))
			h.update(item.object.get_hash())

		return h

	def set(self, path : str, obj):
		split = path.partition('/')

		old_item = self.dict.get(split[0])
		if split[2]:
			t = old_item
			if t is None or not t.object.is_dir():
				# object with this name either didn't exist or was not a tree
				t = type(self)()
			else:
				t = t.object
			obj = t.set(split[2], obj)

		if old_item is not None \
			and old_item.object.svn_sha1 is not None \
			and old_item.object.svn_sha1 == obj.svn_sha1:
			# no changes
			return self

		self = self.make_unshared()

		if old_item is not None:
			self.items.remove(old_item)
		new_item = self.item(split[0], obj)
		self.items.append(new_item)
		self.dict[split[0]] = new_item
		return self

	### find_path(path) finds a tree item (a file or directory) by its path
	def find_path(self, path):
		t = self
		split = iter(path.split('/'))
		next_name = next(split, None)
		while next_name is not None:
			if not t.is_dir():
				return None
			if next_name:
				t = t.dict.get(next_name)
				if t is None:
					return None

				t = t.object

			next_name = next(split, None)
			continue

		return t

	### delete() function removes an item on the given path of arbitrary length.
	# It returns the modified tree, which can be a newly made "unshared" tree,
	# or the original modified tree object.
	# If the path not found, the function returns None
	def delete(self, path : str):
		split = path.partition('/')

		old_item = self.dict.get(split[0])

		if old_item is None:
			return None		# no changes

		self = self.make_unshared()

		if not split[2]:
			self.items.remove(old_item)
			self.dict.pop(split[0])
			return self

		if not old_item.object.is_dir():
			# sub-object doesnt'exist or not a directory
			return None

		# the subdirectory exists
		new_subtree = old_item.object.delete(split[2])
		if not new_subtree:
			return None

		self.items.remove(old_item)
		new_item = self.item(split[0], new_subtree)
		self.items.append(new_item)
		self.dict[split[0]] = new_item
		return self

	### makes the tree into a printable string
	def __str__(self, prefix=''):
		return prefix + '/\n' + '\n'.join((item.object.__str__(prefix + '/' + item.name) for item in self.items))

class history_revision:
	def __init__(self, dump_revision, prev_revision):
		self.dump_revision = dump_revision
		self.log = dump_revision.log
		self.author = dump_revision.author
		self.datetime = dump_revision.datetime
		self.tree = None
		self.rev = dump_revision.rev
		self.prev_rev = prev_revision
		return

class history_reader:

	def __init__(self, options, tree_type=svn_tree, blob_type=svn_blob):
		self.revisions = []
		self.last_rev = None
		self.head = None
		self.tree_type = tree_type
		self.blob_type = blob_type
		self.obj_dictionary = {}
		self.empty_tree = self.finalize_object(tree_type())
		self.options = options
		self.quiet = getattr(options, 'quiet', False)
		self.progress = getattr(options, 'progress', 1.)

		return

	def HEAD(self):
		if len(self.revisions) == 0:
			return None
		else:
			return self.revisions[-1]

	def get_head_tree(self, revision):
		head = self.HEAD()
		if head:
			return head.tree
		else:
			return self.empty_tree

	def finalize_object(self, obj):
		return obj.finalize(self.obj_dictionary)

	def apply_revision(self, revision):
		# Apply the revision to the previous revision.
		# go through nodes in the revision, and apply the action to the history streams
		for node in revision.dump_revision.nodes:
			try:
				revision.tree = self.apply_node(node, revision.tree)
				self.update_progress(revision.rev)
			except Exception_history_parse as e:
				strerror = "NODE %s Path: %s, action: %s" % (
					node.kind.decode() if node.kind is not None else '', node.path, node.action.decode())
				if node.copyfrom_path is not None:
					strerror += ", copy from: %s;%s" % (node.copyfrom_path, node.copyfrom_rev)
				e.strerror = strerror + '\n' + e.strerror
				raise

		revision.tree = self.finalize_object(revision.tree)

		return revision

	def get_revision(self, rev):
		rev = int(rev)
		if rev >= len(self.revisions):
			raise Exception_history_parse('Source revision r%d out of range' % rev)
		r = self.revisions[rev]
		if not r:
			raise Exception_history_parse('Source revision r%d not found' % rev)
		return r

	def apply_dir_node(self, node, base_tree):
		subtree = base_tree.find_path(node.path)

		if node.action == b'add':
			# The directory must not currently exist
			if subtree is not None:
				raise Exception_history_parse('Directory add operation for an already existing directory "%s"' % node.path)
		elif subtree is None:
			raise Exception_history_parse('Directory %s operation for a non-existent path "%s"' % (node.action.decode(), node.path))
		elif not subtree.is_dir():
			raise Exception_history_parse('Directory %s target "%s" is not a directory' % (node.action.decode(), node.path))

		if node.action == b'delete':
			return base_tree.delete(node.path)

		if node.action != b'change':
			if node.copyfrom_path is None:
				subtree = type(base_tree)()
			else:
				copy_source_rev = self.get_revision(node.copyfrom_rev)
				if copy_source_rev is None:
					raise Exception_history_parse('Directory copy revision %s not found' % (node.copyfrom_rev))
				subtree = copy_source_rev.tree.find_path(node.copyfrom_path)
				if subtree is None:
					raise Exception_history_parse('Directory copy source "%s" not found in rev %s' % (node.copyfrom_path, node.copyfrom_rev))

				if not subtree.is_dir():
					raise Exception_history_parse('Directory copy source "%s" in rev %s is not a directory' % (node.copyfrom_path, node.copyfrom_rev))

				subtree = subtree.copy()

		if node.props is not None:
			subtree = subtree.set_properties(node.props, node.props_is_delta)

		return base_tree.set(node.path, subtree)

	def apply_file_node(self, node, base_tree):
		new_properties = None
		delta_base_properties = None
		file_blob = base_tree.find_path(node.path)
		source_file = file_blob

		if node.action != b'add':
			if file_blob is None:
				raise Exception_history_parse('File %s operation for a non-existent file "%s"' % (node.action.decode(), node.path))

			if not file_blob.is_file():
				raise Exception_history_parse('File %s target "%s" is not a file' % (node.action.decode(), node.path))
			delta_base_properties = file_blob.properties
		elif file_blob:
			# The file must not currently exist
			raise Exception_history_parse('File add operation for an already existing file "%s"' % node.path)

		if node.action == b'delete':
			return base_tree.delete(node.path)

		if node.copyfrom_path is not None:
			copy_source_rev = self.get_revision(node.copyfrom_rev)
			if copy_source_rev is None:
				raise Exception_history_parse('File copy revision %s not found' % (node.copyfrom_rev))
			source_file = copy_source_rev.tree.find_path(node.copyfrom_path)
			if source_file is None:
				raise Exception_history_parse('File copy source "%s" not found in rev %s' % (node.copyfrom_path, node.copyfrom_rev))

			if not source_file.is_file():
				raise Exception_history_parse('File copy source "%s;r%s" is not a file' % (node.copyfrom_path, node.copyfrom_rev))

			delta_base_properties = source_file.properties

		if node.text_is_delta:
			if source_file is not None:
				delta_base = source_file.data
				base_sha1 = source_file.data_sha1
			else:
				delta_base = bytes()
				base_sha1 = None

			if base_sha1 and node.text_delta_base_sha1 and base_sha1 != node.text_delta_base_sha1:
				print("WARNING: Delta base SHA-1 doesn't match", file=sys.stderr)
			text_content = node.apply_delta(delta_base)
		else:
			text_content = node.text_content

		if node.action == b'change':
			# 'change' operation preserves the original file instead of copying properties from the source
			new_properties = file_blob.properties
			delta_base_properties = new_properties
		elif text_content is not None and source_file:
			new_properties = source_file.properties
			delta_base_properties = new_properties

		if node.props_is_delta:
			new_properties = apply_delta_to_properties(delta_base_properties, node.props)
		elif node.props is not None:
			new_properties = node.props

		if text_content is not None:
			file_blob = self.make_blob(text_content, node, new_properties)
		else:
			if source_file:
				file_blob = source_file
			if new_properties is not None:
				file_blob = self.copy_blob(file_blob, node, new_properties)

		return base_tree.set(node.path, self.finalize_object(file_blob))

	def make_blob(self, data, node, properties):
		# node.path can be used by a hook to apply proper path-specific Git attributes
		# Make a bare svn_blob for the given data, or use an existing clone

		obj = self.blob_type(properties=properties)
		obj.data_len = len(data)
		obj.data = data

		# text_sha1 is blob sha1 hash as 40 chars hex string. if it's not supplied, it's calculated
		if node:
			text_sha1 = node.text_content_sha1
		else:
			text_sha1 = None

		if text_sha1:
			obj.data_sha1 = text_sha1
		else:
			obj.data_sha1 = make_data_sha1(data).digest()

		# finalize will calculate SVN hash and possibly
		# return an existing bare object instead of the one we just created
		if properties is not None:
			obj.properties = properties.copy()

		# finalize will calculate SVN hash and possibly
		# return an existing object instead of the one we just created
		obj = self.finalize_object(obj)

		return obj

	# node passed to be used in the derived class overrides
	def copy_blob(self, src_obj, node, properties):
		obj = type(src_obj)(src=src_obj, properties=properties)

		# finalize will calculate SVN hash and possibly
		# return an existing object instead of the one we just created
		return self.finalize_object(obj)

	def apply_node(self, node, base_tree):
		action = node.action

		if action == b'replace':
		# Simulate replace through delete and add:
			base_tree = base_tree.delete(node.path)
			if not base_tree:
				raise Exception_history_parse('Replace operation for a non-existent path "%s"' % node.path)
			node.action = b'add'

		if node.kind == b'dir':
			return self.apply_dir_node(node, base_tree)
		elif node.kind == b'file':
			return self.apply_file_node(node, base_tree)
		elif action == b'delete':
			# Delete operation comes without node kind specified
			new_tree = base_tree.delete(node.path)
			if not new_tree:
				raise Exception_history_parse('Delete operation for a non-existent path "%s"' % node.path)
		else:
			raise Exception_history_parse("None-kind node allows only 'delete' action, got '%s' instead" % node.action)

		node.action = action
		return new_tree

	def print_progress_message(self, msg, end=None):
		if not self.quiet:
			print(msg, end=end, file=sys.stderr)
		return

	def update_progress(self, rev):
		if self.progress is not None and time.monotonic() - self.last_progress_time >= self.progress:
			self.print_progress_line(rev)
			self.last_progress_time = time.monotonic()
		return

	def print_progress_line(self, rev):
		if rev != self.last_rev:
			self.print_progress_message("Processing revision %s" % rev, end='\r')
			self.last_rev = rev
		return

	def print_last_progress_line(self):
		elapsed = datetime.timedelta(seconds=time.monotonic() - self.start_time)
		self.print_progress_message("Processed %d revisions in %s" % (self.total_revisions, str(elapsed)))
		return

	### load function loads SVN dump from the given 'revision_reader' generator function
	# The history is then reconstructed by apply_revision() in form of full trees.
	# If 'log_dump' is set in options, the headers and revisions are printed to options.logfile
	def load(self, revision_reader):
		log_file = getattr(self.options, 'log_file', sys.stdout)
		log_dump = getattr(self.options, 'log_dump', True)
		end_revision = getattr(self.options, 'end_revision', None)
		verify_data_hash = getattr(self.options, 'verify_data_hash', True)

		if end_revision is not None:
			end_revision = int(end_revision)

		self.total_revisions = 0
		self.last_progress_time = 0.
		self.start_time = time.monotonic()

		prev_revision = None
		prev_rev = None
		rev = None
		try:
			for dump_revision in revision_reader.read_revisions(verify_data_hash):
				rev = dump_revision.rev

				if prev_rev is not None:
					if rev <= prev_rev:
						raise Exception_history_parse("Previous revision was %d" % prev_rev)
					if rev > prev_rev + 1:
						print("WARNING: Revision %d is followed by revision %d" % (prev_rev, rev), file=sys.stderr)
				prev_rev = rev

				revision = history_revision(dump_revision, prev_revision)
				revision.tree = self.get_head_tree(revision)

				total_revs = len(self.revisions)
				if rev > total_revs:
					self.revisions += [None] * (rev - total_revs)
				self.revisions.append(revision)

				self.update_progress(rev)

				if log_dump:
					dump_revision.print(log_file)

				self.apply_revision(revision)
				self.total_revisions += 1

				if end_revision is not None and rev >= end_revision:
					break

				# Don't keep the dump data anymore
				revision.dump_revision = None
				prev_revision = revision
				continue

			self.print_last_progress_line()

		except:
			if rev is not None:
				print("\nInterrupted at revision %s" % rev, file=sys.stderr)
			raise
		return self
