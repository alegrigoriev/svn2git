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
import io
import re
import hashlib
import zlib
import datetime
from exceptions import Exception_svn_parse

# These are statistics counters:
TEXT_CONTENT_SHA1_SPECIFIED = 0
TEXT_CONTENT_MD5_SPECIFIED = 0
TEXT_CONTENT_SHA1_CALCULATED_FILES = 0
TEXT_CONTENT_MD5_CALCULATED_FILES = 0
TEXT_CONTENT_SHA1_CALCULATED_SIZE = 0
TEXT_CONTENT_MD5_CALCULATED_SIZE = 0

TOTAL_BYTES_READ=0
TOTAL_LINES_READ=0

TEXT_CONTENT_DELTA_APPLIED_FILES = 0
TEXT_CONTENT_NON_TRIVIAL_DELTA_APPLIED_FILES = 0
TEXT_CONTENT_DELTA_APPLIED_SIZE = 0
TEXT_CONTENT_DELTA_ZLIB_DECODED_BLOCKS = 0
TEXT_CONTENT_DELTA_ZLIB_DECODED_SIZE = 0
TEXT_CONTENT_DELTA_LZ4_DECODED_BLOCKS = 0
TEXT_CONTENT_DELTA_LZ4_DECODED_SIZE = 0

def make_data_sha1(data):
	global TEXT_CONTENT_SHA1_CALCULATED_FILES
	global TEXT_CONTENT_SHA1_CALCULATED_SIZE
	h = hashlib.sha1()
	h.update(data)
	TEXT_CONTENT_SHA1_CALCULATED_FILES += 1
	TEXT_CONTENT_SHA1_CALCULATED_SIZE += len(data)
	return h

def make_data_md5(data):
	global TEXT_CONTENT_MD5_CALCULATED_FILES
	global TEXT_CONTENT_MD5_CALCULATED_SIZE
	h = hashlib.md5()
	h.update(data)
	TEXT_CONTENT_MD5_CALCULATED_FILES += 1
	TEXT_CONTENT_MD5_CALCULATED_SIZE += len(data)
	return h

def read_line(fd):
	line = fd.readline()
	global TOTAL_BYTES_READ, TOTAL_LINES_READ
	TOTAL_LINES_READ +=1
	TOTAL_BYTES_READ += len(line)
	#the line ends with '\n'
	line.strip(b'\n')

	return line

def read_content(fd, length):
	d = fd.read(length)
	global TOTAL_BYTES_READ, TOTAL_LINES_READ
	if len(d) != length:
		raise Exception_svn_parse("Expected to read %d bytes of contents at line %d, read %d bytes instead" % (length, TOTAL_LINES_READ, len(d)))

	if fd.read(1) != b'\n':
		raise Exception_svn_parse("Newline missing after contents block at line %d" % (TOTAL_LINES_READ))

	TOTAL_BYTES_READ += length + 1
	TOTAL_LINES_READ += d.count(b'\n') + 1

	return d

# Read attributes from fd, and create nodes as it goes
class dump_record:
	def __init__(self):
		self.tuples = []
		self.dict = {}
		return
	def __iter__(self):
		return self.tuples.__iter__()
	def __getitem__(self, key:int):
		return self.tuples[key]
	def type(self)->bytes:
		if len(self.tuples):
			return self.tuples[0][1]
		else:
			return None

	def read(self, fd):

		line = read_line(fd)
		while line == b'\n':
			# there can be multiple blank lines after Node record. Apparently, the dump
			# routine inserts those even after non-present properties and text sections
			line = read_line(fd)
		if line == b'':	# EOF
			return None

		while line != b'\n':
			if line == b'':	# EOF
				break;

			split = line.rstrip(b'\n').split(b': ', 1)
			if len(split) != 2:
				raise Exception_svn_parse("Expected record line in format \"type: value\", read:\"%s\" instead" % line.decode().rstrip('\n'))

			tag = split[0]
			value = split[1]
			self.tuples.append((line, tag, value))
			if tag in self.dict:
				print("WARNING: Duplicate line \"%s\" in record block \"%s\"" % (tag.decode(), self[0][0].decode()), file=sys.stderr)
			self.dict[tag] = value
			line = read_line(fd)

		return self

def validate_dump_version_record(record):
	if record is None:
		raise Exception_svn_parse("Expected single line dump-format-version record, encountered EOF instead")

	if len(record.tuples) != 1:
		raise Exception_svn_parse("Expected single line dump-format-version record, read %d lines instead" % len(record))

	if record.type() != b"SVN-fs-dump-format-version":
		raise Exception_svn_parse("Expected dump-format-version record, read:\"%s\" instead" % record[0][0])

	if record[0][2] != b"2" and record[0][2] != b"3":
		raise Exception_svn_parse("dump-format-version \"%s\" is not 2 or 3"
									% record[0][2].decode())

	return record

def validate_UUID_record(record):
	if record is not None and record.type() == b"UUID":
		# This is UUID record, for example: "UUID: 0327ee65-5647-43ce-affc-0d4a17cd70ef"
		if not re.fullmatch(b"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}", record[0][2]):
			raise Exception_svn_parse("UUID \"%s\" is not a proper UUID line"
										% record[0][2].decode())

		return record
	else:
		return None

class revision_record:
	def __init__(self, record, fd):
		self.version_record = None
		self.uuid_record = None
		# Note that 'props' dictionary keeps keys as text strings and values as byte arrays
		self.props = {}
		self.nodes = []

		# We have a record here. It's expected to be a revision block
		if record.type() != b"Revision-number":
			raise Exception_svn_parse("Expected Revision-number record, read:\"%s\" instead"
									% record[0][0].decode())

		rev_num = record[0][2]
		if not re.fullmatch(rb"\d+", rev_num):
			raise Exception_svn_parse("Expected decimal revision number, read \"%s\" instead"
										% rev_num.decode())

		content_len = 0
		self.rev = int(rev_num)
		prop_content_len = 0
		#check that all lines in the record block are known to us:
		for (line, tag, value) in record[1:]: # skip the first line, which we already validated
			if tag == b"Prop-content-length":
				prop_content_len = int(value)
			elif tag == b"Content-length":
				content_len = int(value)
			else:
				print("WARNING: Unknown line \"%s\" in \"%s\" block"
						% (line.decode(), record[0][0].decode()), file=sys.stderr)

		if record[-1][1] != b"Content-length":
			raise Exception_svn_parse("Last line in revision record must be \"Content-length\", read \"%s\" instead"
								% record[-1][0].decode())

		if content_len:
			content = read_content(fd, content_len)
			if prop_content_len:
				process_props_block(self, content[0:prop_content_len])

		self.date = self.props.pop(b'svn:date', b'').decode()
		if self.date:
			# fromisoformat() doesn't recognize Zulu symbol. Add explicit timezone 00
			self.datetime = datetime.datetime.fromisoformat(self.date.rstrip('Z') + '+00:00')
		else:
			self.datetime = None

		self.log = self.props.pop(b'svn:log', b'').decode()
		self.author = self.props.pop(b'svn:author', b'').decode()
		return

	def print(self, fd=sys.stdout):

		if self.version_record:
			print("VERSION: \"%s\"" % self.version_record[0][2].decode(), file=fd)

		if self.uuid_record:
			print("   UUID: \"%s\"" % self.uuid_record[0][2].decode(), file=fd)

		# Note that 'props' dictionary keeps keys as text strings and values as byte arrays,
		#  therefore decode() needs to be called
		print("REVISION: %s, time: %s, author: %s" % (self.rev, self.date, self.author), file=fd)

		if self.log:
			print("MESSAGE: %s" % ("\n         ".join(self.log.splitlines())), file=fd)
		# Note that 'props' dictionary keeps keys as text strings and values as byte arrays
		for prop, value in self.props.items():
			print("       PROP: %s=\"%s\"" % (prop.decode(), "\n         ".join(value.decode().splitlines())), file=fd)

		for node in self.nodes:
			node.print(fd)

		print("", file=fd)
		return

def decode_int_value(tag:bytes, value:bytes):
	if not re.fullmatch(rb'\d+', value):
		raise Exception_svn_parse("'%s: %s', expected a decimal number" % (tag.decode(), value.decode()))
	return int(value)

def decode_bool_value(tag:bytes, value:bytes):
	if value != b'true' and value != b'false':
		raise Exception_svn_parse("'%s: %s', must be either 'true' or 'false'" % (tag.decode(), value.decode()))
	return bool(value)

def decode_sha1_value(tag:bytes, value:bytes):
	value = value.decode()
	if not re.fullmatch('[0-9A-Fa-f]{40}', value):
		raise Exception_svn_parse("'%s: %s', expected string of 40 hexadecimal numbers" % (tag.decode(), value))
	return bytes.fromhex(value)

def decode_md5_value(tag:bytes, value:bytes):
	value = value.decode()
	if not re.fullmatch('[0-9A-Fa-f]{32}', value):
		raise Exception_svn_parse("'%s: %s', expected string of 32 hexadecimal numbers" % (tag.decode(), value))
	return bytes.fromhex(value)

class node_record:
	def __init__(self):
		# Note that 'props' dictionary keeps keys as text strings and values as byte arrays
		self.props = None
		self.kind = None
		self.action = None
		self.copyfrom_path = None
		self.copyfrom_rev = None
		# The hashes are hex strings
		self.copy_source_md5 = None
		self.copy_source_sha1 = None
		self.text_content_md5 = None
		self.text_content_sha1 = None
		self.text_is_delta = False
		self.props_is_delta = False
		self.text_delta_base_md5 = None
		self.text_delta_base_sha1 = None
		self.text_content = None

		return

	def read(node, record, fd, verify_data_hash):
		node.path = record[0][2].decode()

		prop_content_len = None
		text_content_len = None
		content_len = None

		# Each Revision record is followed by one or more Node records.
		# Node records have the following sequence of header lines:
		# 
		# -------------------------------------------------------------------
		# Node-path: <path/to/node/in/filesystem>
		# [Node-kind: {file | dir}]
		# Node-action: {change | add | delete | replace}
		# [Node-copyfrom-rev: <rev>]
		# [Node-copyfrom-path: <path> ]
		# [Text-copy-source-md5: <blob>]
		# [Text-copy-source-sha1: <blob>]
		# [Text-content-md5: <blob>]
		# [Text-content-sha1: <blob>]
		# [Text-content-length: <T>]
		# [Prop-content-length: <P>]
		# [Content-length: Y]
		# 
		# -------------------------------------------------------------------
		for (line, tag, value) in record[1:]: # skip the first line, which we already validated
			if tag == b"Node-kind":
				if value != b'file' and value != b'dir':
					raise Exception_svn_parse("Node-kind: '%s', must be either 'file' or 'dir'" % value.decode())

				node.kind = value
			elif tag == b"Node-action":
				if value != b'change' and value != b'add' and value != b'delete' and value != b'replace':
					raise Exception_svn_parse("Node-action: '%s', must be either 'add' or 'delete' or 'change' or 'replace'" % value.decode())

				node.action= value
			elif tag == b"Text-content-length":
				text_content_len = decode_int_value(tag, value)
			elif tag == b"Content-length":
				content_len = decode_int_value(tag, value)
			elif tag == b"Node-copyfrom-rev":
				node.copyfrom_rev = decode_int_value(tag, value)
			elif tag == b"Node-copyfrom-path":
				node.copyfrom_path = value.decode()
			elif tag == b"Text-content-sha1":
				node.text_content_sha1 = decode_sha1_value(tag, value)
				global TEXT_CONTENT_SHA1_SPECIFIED
				TEXT_CONTENT_SHA1_SPECIFIED += 1
			elif tag == b"Text-content-md5":
				node.text_content_md5 = decode_md5_value(tag, value)
				global TEXT_CONTENT_MD5_SPECIFIED
				TEXT_CONTENT_MD5_SPECIFIED += 1
			elif tag == b"Text-copy-source-md5":
				node.copy_source_md5 = decode_md5_value(tag, value)
			elif tag == b"Text-copy-source-sha1":
				node.copy_source_sha1 = decode_sha1_value(tag, value)
			elif tag == b"Prop-content-length":
				prop_content_len = decode_int_value(tag, value)
			# 2. There are several new optional headers for Node records:
			# 
			# -------------------------------------------------------------------
			# [Text-delta: true|false]
			# [Prop-delta: true|false]
			# [Text-delta-base-md5: blob]
			# [Text-delta-base-sha1: blob]
			# [Text-content-md5: blob]
			# [Text-content-sha1: blob]
			# -------------------------------------------------------------------

			elif tag == b"Text-delta":
				node.text_is_delta = decode_bool_value(tag, value)
			elif tag == b"Prop-delta":
				node.props_is_delta = decode_bool_value(tag, value)
			elif tag == b"Text-delta-base-sha1":
				node.text_delta_base_sha1 = decode_sha1_value(tag, value)
			elif tag == b"Text-delta-base-md5":
				node.text_delta_base_md5 = decode_md5_value(tag, value)
			else:
				print("WARNING: Unknown line \"%s\" in \"%s\" block" % (line.decode(), record[0][0].decode()), file=sys.stderr)

		if content_len is not None:
			content = read_content(fd, content_len)
			if prop_content_len:
				process_props_block(node, content[0:prop_content_len])
			else:
				prop_content_len = 0
			if text_content_len is not None:
				if content_len != prop_content_len + text_content_len:
					raise Exception_svn_parse("Content-length=%s, not equal to sum of Prop-content-length=%s and Text-content-length=%s" % \
						(content_len, prop_content_len, text_content_len))
				node.text_content = content[prop_content_len:]

				if not node.text_is_delta and verify_data_hash:
					if node.text_content_sha1:
						if make_data_sha1(node.text_content).digest() != node.text_content_sha1:
							raise Exception_svn_parse("Path \"%s\": Text-content-sha1 doesn't match" % node.path)
					elif node.text_content_md5:
						# only checking MD5 if SHA1 is not given
						if make_data_md5(node.text_content).digest() != node.text_content_md5:
							raise Exception_svn_parse("Path \"%s\": Text-content-md5 doesn't match" % node.path)

		elif text_content_len or prop_content_len:
			raise Exception_svn_parse("Content-length=%s, not equal to sum of Prop-content-length=%s and Text-content-length=%s" % \
				(content_len, prop_content_len, text_content_len))

		# validate kind, action and data:
		if node.kind == b'dir':
			if node.text_content is not None:
				raise Exception_svn_parse("A directory node must not have text content")

			if node.action == b'change':
				if not prop_content_len:
					raise Exception_svn_parse("A directory change operation must supply props content")

		if node.action == b'delete':
			if node.text_content is not None:
				raise Exception_svn_parse("A delete operation cannot supply text content")

			if prop_content_len:
				raise Exception_svn_parse("A delete operation cannot supply props content")

			if node.copyfrom_path is not None:
				raise Exception_svn_parse("A delete operation cannot supply copy source")

		elif node.action == b'change':
			if node.copyfrom_path is not None:
				raise Exception_svn_parse("A change operation cannot supply copy source")

		return node

	def print(node, fd=sys.stdout):
		print("   NODE %s %s:%s" % (node.action.decode(),
					node.kind.decode() if node.kind is not None else None, node.path), file=fd)
		if node.copyfrom_rev:
			print("       COPY FROM: %s;r%s" % (node.copyfrom_path, node.copyfrom_rev), file=fd)

		# Note that 'props' dictionary keeps keys and values as byte arrays
		if node.props is not None:
			for key, prop in node.props.items():
				key = key.decode()
				if prop is not None:
					prop = prop.decode()
					print("       PROP: %s=\"%s\"" % (key, "\n         ".join(prop.splitlines())), file=fd)
				else:
					print("       DELETE PROP: %s" % (key), file=fd)
				continue
		return

	def apply_delta(node, base_text):

		def get_int(fd, eof_ok=False):
			num = 0
			byte = fd.read(1)
			if eof_ok and len(byte) == 0:
				return None	# end of data
			while len(byte) == 1:
				byte = byte[0]
				num = (num << 7) + (byte & 0x7F)
				if 0 == (byte & 0x80):
					return num
				byte = fd.read(1)

			raise Exception_svn_parse("End of delta block encountered when reading an encoded number")

		def get_data(fd, length, ver):
			if ver == 0:
				data = fd.read(length)
				if len(data) != length:
					raise Exception_svn_parse("End of delta block encountered when reading data")
				return data
			original_len = get_int(fd)
			if length == original_len:
				return data
			if ver == 1:
				#zlib
				data = zlib.decompress(data)

				global TEXT_CONTENT_DELTA_ZLIB_DECODED_BLOCKS, TEXT_CONTENT_DELTA_ZLIB_DECODED_SIZE
				TEXT_CONTENT_DELTA_ZLIB_DECODED_BLOCKS += 1
				TEXT_CONTENT_DELTA_ZLIB_DECODED_SIZE += len(data)
			else:
				#LZ4
				try:
					import lz4
				except ModuleNotFoundError:
					# use PyPl to install lz4 PIP package
					raise Exception_svn_parse(f'lz4 module not found, use "{sys.executable} -m pip install lz4" to install it')
				data = lz4.frame.decompress(data)

				global TEXT_CONTENT_DELTA_LZ4_DECODED_BLOCKS, TEXT_CONTENT_DELTA_LZ4_DECODED_SIZE
				TEXT_CONTENT_DELTA_LZ4_DECODED_BLOCKS += 1
				TEXT_CONTENT_DELTA_LZ4_DECODED_SIZE += len(data)

			if len(data) != original_len:
				raise Exception_svn_parse("Decompressed delta data length doesn't match expected")
			return data

		fd = io.BytesIO(node.text_content)

		# An svndiff document begins with four bytes, "SVN" followed by a byte
		# which represents a format version number.  After the header come one
		# or more windows, until the document ends.  (So the decoder must have
		# external context indicating when there is no more svndiff data.)
		# 
		# A window is the concatenation of the following:
		# 
		#      The source view offset
		#      The source view length
		#      The target view length
		#      The length of the instructions section in bytes
		#      The length of the new data section in bytes
		#      [original length of the instructions section in bytes (version 1)]
		#      The window's instructions section
		#      [original length of the new data section in bytes (version 1)]
		#      The window's new data section

		header = fd.read(4)
		if len(header) != 4 or header[0:3] != b'SVN':
			raise Exception_svn_parse("Delta block doesn't start with 'SVN' <version>")
		version = int(header[3])
		if version > 2:
			raise Exception_svn_parse("Delta block unsupported version %d" % version)

		target_data = bytearray()
		total_instructions_executed = 0
		total_trivial_instructions = 0

		while True:
			source_offset = get_int(fd, eof_ok = True)
			if source_offset is None:
				break

			source_view_length = get_int(fd)
			target_view_length = get_int(fd)
			instructions_length = get_int(fd)
			data_length = get_int(fd)

			if source_offset + source_view_length > len(base_text):
				raise Exception_svn_parse("Delta decoding: source offset outside data")

			instructions = get_data(fd, instructions_length, version)
			data = get_data(fd, data_length, version)

			instr_fd = io.BytesIO(instructions)
			data_fd = io.BytesIO(data)
			target_view = bytearray()

			while True:
				instruction = instr_fd.read(1)
				if len(instruction) != 1:
					break

				opcode = instruction[0] & 0xC0
				copy_len = instruction[0] & 0x3F
				if copy_len == 0:
					copy_len = get_int(instr_fd)
				if opcode == 0x00:
					#copy from source view
					offset = get_int(instr_fd)
					if offset == 0 and source_offset == 0 and copy_len == target_view_length:
						total_trivial_instructions += 1
					if offset + copy_len > source_view_length:
						raise Exception_svn_parse("Copy from source view: source not inside view")
					offset += source_offset
					target_view += base_text[offset : offset + copy_len]
				elif opcode == 0x40:
					# copy from target view
					offset = get_int(instr_fd)
					if offset >= len(target_view):
						raise Exception_svn_parse("Copy from target view: source not inside data")
					while copy_len:
						to_copy = copy_len
						if offset + to_copy > len(target_view):
							to_copy = len(target_view) - offset
						target_view += target_view[offset:offset+to_copy]
						copy_len -= to_copy
						offset += to_copy
				elif opcode == 0x80:
					if copy_len == target_view_length:
						total_trivial_instructions += 1
					# copy from new (immediate) data
					to_copy = data_fd.read(copy_len)
					read_len = len(to_copy)
					if read_len != copy_len:
						raise Exception_svn_parse("Insufficient length of immediate data in delta block")
					target_view += to_copy
				else:
					raise Exception_svn_parse("Delta block unsupported op selector %X" % opcode)
				total_instructions_executed += 1

			if len(target_view) != target_view_length:
				raise Exception_svn_parse("Recreated target view lengths doesn't match expected" % opcode)

			target_data += target_view
			if len(instructions) != instructions_length or len(data) != data_length:
				# compression was used, thus the expansion was not trivial single copy:
				total_trivial_instructions = 0

		global TEXT_CONTENT_DELTA_APPLIED_FILES, TEXT_CONTENT_NON_TRIVIAL_DELTA_APPLIED_FILES, TEXT_CONTENT_DELTA_APPLIED_SIZE
		TEXT_CONTENT_DELTA_APPLIED_FILES += 1
		if total_trivial_instructions != 1 or total_instructions_executed != 1:
			TEXT_CONTENT_NON_TRIVIAL_DELTA_APPLIED_FILES +=1
			TEXT_CONTENT_DELTA_APPLIED_SIZE += len(node.text_content)

		return target_data

def process_props_block(owner, props):
	if owner.props is None:
		owner.props = {}
	fd = io.BytesIO(props)
	while True:
		line = fd.readline()
		if line == b'PROPS-END\n':
			# This is a marker of end of property stream
			return

		if line == b'':
			raise Exception_svn_parse("Property block at line %d is not properly terminated by PROPS-END line"
								% (line.decode().rstrip('\n'), TOTAL_LINES_READ))

		m = re.fullmatch(b"(K|D)\s+(\d+)\n", line)
		if not m:
			raise Exception_svn_parse("Unable to decode line \"%s\" in a property block at line %d, K/D <size> line expected"
								% (line.decode().rstrip('\n'), TOTAL_LINES_READ))

		command = m.group(1)
		key_name_size = int(m.group(2))
		# the next line is the key name
		key_name = fd.read(key_name_size)
		if fd.read(1) != b'\n':
			raise Exception_svn_parse("Newline missing after key name \"%s\" in a property block at line %d"
									% (key_name.decode(), TOTAL_LINES_READ))

		if key_name in owner.props:
			raise Exception_svn_parse("Duplicate key name \"%s\" in a property block at line %d"
									% (key_name.decode(), TOTAL_LINES_READ))

		if command == b'D':
			owner.props[key_name] = None
		else:
			line = fd.readline()
			m = re.fullmatch(b"V\s+(\d+)\n", line)
			if not m:
				raise Exception_svn_parse("Unable to decode line \"%s\" in a property block at line %d, V <size> line expected"
								% (line.decode().rstrip('\n'), TOTAL_LINES_READ))

			value_size = int(m.group(1))
			owner.props[key_name] = fd.read(value_size)
			if fd.read(1) != b'\n':
				raise Exception_svn_parse("Newline missing after value for key \"%s\" in a property block at line %d"
									% (key_name.decode(), TOTAL_LINES_READ))
		continue

	return

class svn_dump_reader:
	def __init__(self, filename):
		self.filename = filename
		self.fd = open(filename, 'rb')

		return

	def read_revisions(self, verify_data_hash=False):
		# dumpfile consists of four kinds of records.  A record is a group of
		# RFC822-style header lines (each consisting of a key, followed by a
		# colon, followed by text data to end of line), followed by an empty
		# spacer line, followed optionally by a body section.  If the body
		# section is present, another empty spacer line separates it from the
		# following record.

		try:
			node = None
			revision = None
			#First record is dump version (currently 3): "SVN-fs-dump-format-version: 3"
			version_record = validate_dump_version_record(dump_record().read(self.fd))

			record = dump_record().read(self.fd)
			uuid_record = validate_UUID_record(record)
			if uuid_record is not None:
				record = dump_record().read(self.fd)

			while record is not None:
				node = None
				revision = None
				revision = revision_record(record, self.fd)
				revision.version_record = version_record
				version_record = None
				revision.uuid_record = uuid_record
				uuid_record = None

				record = dump_record().read(self.fd)

				while record is not None and record.type() == b'Node-path':
					node = node_record().read(record, self.fd, verify_data_hash)
					revision.nodes.append(node)
					record = dump_record().read(self.fd)

				yield revision
				continue

		except Exception_svn_parse as ex:
			if node is not None:
				ex.strerror = ("NODE %s: \n" % node.path) + ex.strerror
			if revision is not None:
				ex.strerror = ("REVISION %d: " % revision.rev) + ex.strerror
			raise
		return

def print_stats(fd):
	print("Processed %d lines, %d MiB of data" % (TOTAL_LINES_READ, TOTAL_BYTES_READ//0x100000), file=fd)
	if TEXT_CONTENT_MD5_SPECIFIED:
		print("MD5: specified %d times, calculated %d times, %d MiB" % (
			TEXT_CONTENT_MD5_SPECIFIED, TEXT_CONTENT_MD5_CALCULATED_FILES, TEXT_CONTENT_MD5_CALCULATED_SIZE//0x100000), file=fd)
	if TEXT_CONTENT_SHA1_SPECIFIED:
		print("SHA1: specified %d times, calculated %d times, %d MiB" % (
			TEXT_CONTENT_SHA1_SPECIFIED, TEXT_CONTENT_SHA1_CALCULATED_FILES, TEXT_CONTENT_SHA1_CALCULATED_SIZE//0x100000), file=fd)
	if TEXT_CONTENT_DELTA_APPLIED_FILES:
		print("DELTA: applied %d times (%d non-trivial expansions), %d KiB" % (TEXT_CONTENT_DELTA_APPLIED_FILES,
			TEXT_CONTENT_NON_TRIVIAL_DELTA_APPLIED_FILES, TEXT_CONTENT_DELTA_APPLIED_SIZE//0x400), file=fd)
	if TEXT_CONTENT_DELTA_ZLIB_DECODED_BLOCKS:
		print("DELTA zlib: decompressed %d times, %d KiB" % (
			TEXT_CONTENT_DELTA_ZLIB_DECODED_BLOCKS, TEXT_CONTENT_DELTA_ZLIB_DECODED_SIZE//0x400), file=fd)
	if TEXT_CONTENT_DELTA_LZ4_DECODED_BLOCKS:
		print("DELTA LZ4: decompressed %d times, %d KiB" % (
			TEXT_CONTENT_DELTA_LZ4_DECODED_BLOCKS, TEXT_CONTENT_DELTA_LZ4_DECODED_SIZE//0x400), file=fd)
	return
