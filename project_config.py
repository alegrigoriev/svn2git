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
from types import SimpleNamespace
import re
import xml.etree.ElementTree as ET
from exceptions import Exception_cfg_parse

### replace vars in ref pattern strings
# If dirs_only then the spec will be forced to only match directories
# Otherwise it will match directories if it ends with a slash.
# Match variant depends on match_dirs and match_files
# If match_dirs=False, match_files=False:
#   A regex for exact literal match will be produced.
#
# If match_dirs=True, match_files=False:
#   A regex will match directories only.
#   The source globspec doesn't need to end with a slash, it's assumed.
#   To match a directory and all items under it, call re.match().
#   To match ONLY a directory, call re.fullmatch().
#
# If match_dirs=False, match_files=True:
#   if the wildcard ends with a slash, the regex will match directories only,
#   otherwise it will match files ONLY. If the regex matches a directory,
#   it also matches all items under it.
#   Call re.fullmatch() for this regex.
#
# If match_dirs=True, match_files=True:
#   if the wildcard ends with a slash, the regex will match directories only,
#   otherwise it will match directories AND files. If the regex matches a directory,
#   it also matches all items under it.
#   Call re.fullmatch() for this regex.
#

def parse_wildcard(spec, repl_list):
	git_wildcard_split_regex = '|'.join(r.pattern for r,x in repl_list)

	regex = ''
	last_pos = 0
	for m in re.finditer(git_wildcard_split_regex, spec):
		if m.start() > last_pos:
			regex += re.escape(spec[last_pos:m.start()])
		last_pos = m.end()

		for (r, repl) in repl_list:
			if r.match(spec, m.start()):
				regex += repl
				break

	regex += re.escape(spec[last_pos:])
	return regex

# The replacement list consisist of tuples of:
#  1) the match object to match the source component, and
#  2) the string to insert to the regex
default_repl_list = [
		# **/ in the beginning of the string, matches zero or more directory components
		(re.compile(r'^\*\*/'), '(.*/)?'),
		# ** in place of a directory component, matches zero or more directory components
		(re.compile(r'(?<=/)\*\*/'), '(.*/)?'),
		# Othersise, two asterisks match any characters
		(re.compile(r'\*\*'), '(.*)'),
		# Asterisk in place of a directory level needs one or more characters matching
		(re.compile(r'(?<=/)\*/'), '([^/]+)/'),
		# An asterisk with slash at the start of line needs one or more characters matching
		(re.compile(r'^\*/'), '([^/]+)/'),
		# An asterisk after a slash at the end of line needs one or more characters matching
		(re.compile(r'(?<=/)\*\Z'), '([^/]+)'),
		# Otherwise an asterisk matches any numer of characters
	]

match_dirs_only_repl_list = [
		# If doesn't end with a slash, append one
		(re.compile(r'(?<=[^/])\Z'), '/'),
	]

match_files_repl_list = [
		# A pattern ends with a slash: match it as a directory name only 
		(re.compile(r'/\Z'), '/(?:.*)'),
	]

match_dirs_repl_list = [
		# A pattern ends with no slash: match it as filename and as directory name
		(re.compile(r'(?<=[^/])\Z'), '(?:/.*)?'),
	]

literal_match_repl_list = [
		(re.compile(r'\?'), '([^/])'),
		(re.compile(r'\*'), '([^/]*)'),
	]

def make_regex_from_wildcard(spec, match_dirs=False, match_files=False):

	repl_list = default_repl_list

	if match_files:
		if match_dirs:
			repl_list = repl_list + match_dirs_repl_list
		repl_list = repl_list + match_files_repl_list
	elif match_dirs:
		repl_list = repl_list + match_dirs_only_repl_list
	else:
		# match_dirs=False,match_files=False:
		# A regex for exact literal match will be produced.
		return parse_wildcard(spec, literal_match_repl_list)

	# Multiple slashes are replaced with one
	spec = re.sub('//+', '/', spec)

	regex = parse_wildcard(spec, repl_list + literal_match_repl_list)

	before_slash, slash, after_slash = spec.partition('/')

	# If no slash in the spec (except at the end), equivalent to **/spec
	if (match_files or match_dirs) and not (after_slash or before_slash.find('**') != -1):
		# Implicitly prepended **/, no capture
		regex = '(?:.*/)?' + regex

	# Strip the leading slash
	return regex.removeprefix('/')

def replace_vars_for_regex(src, vars_dict={}, match_dirs=False, match_files=False):
	# Replace single vars (not lists) first
	def sub(m):
		subst = vars_dict.get(m[1])
		if not subst or len(subst) != 1:
			return m[0]
		return subst[0]

	expanded = re.sub(r'\$(\w+)', sub, src)

	regex = make_regex_from_wildcard(expanded, match_dirs=match_dirs, match_files=match_files)

	# Replace list vars now
	def sub_list(m):
		subst = vars_dict.get(m[1])
		if not subst or len(subst) == 1:
			return m[0]
		return '(?:' + '|'.join(subst) + ')'

	# At this point, '$' characters will be escaped with a backslash
	return re.sub(r'\\\$(\w+)', sub_list, regex)

### replace vars in ref pattern strings
def replace_vars_for_glob(src, vars_dict={}):
	def sub(m):
		subst = vars_dict.get(m[1])
		if not subst:
			return m[0]
		if len(subst) == 1:
			return subst[0]
		return '{' + ','.join(subst) + '}'

	return re.sub(r'\$(\w+)', sub, src)

### Replace vars in substitution strings
def replace_vars_in_subst(src, vars_dict={}):
	names = [r'\$([1-9])', r'\$(\w+)', r'^\*\*/', r'/\*\*/', r'\*\*', r'\*']
	for var in vars_dict:
		names.append(re.escape(var))
	last_var = 0

	def sub(m):
		nonlocal vars_dict, last_var
		m0 = m[0]
		if m0.startswith('*'):
			last_var += 1
			return '\\' + str(last_var)
		elif m0 == '/**/':
			last_var += 1
			return '/\\' + str(last_var)
		elif m[1]:
			last_var = int(m[1])
			return '\\' + m[1]

		m2 = m[2]
		if not m2:
			return m0
		value = vars_dict.get(m2)
		if not value:
			return m0

		if len(value) > 1:
			print("WARNING: Variable %s with list value %s cannot be used as substitution" % (m0, value), file=sys.stderr)
			return ''
		return value[0]

	return re.sub('|'.join(names), sub, src)

class glob_match:
	def __init__(self, match_pattern, vars_dict={}, match_dirs=False, match_files=False):
		self.match_pattern = match_pattern

		self.regex = replace_vars_for_regex(match_pattern, vars_dict,
									match_dirs=match_dirs, match_files=match_files)
		self.re = re.compile(self.regex)
		self.globspec = replace_vars_for_glob(match_pattern, vars_dict)
		if match_dirs and not match_files and not self.globspec.endswith('/'):
			self.globspec += '/'

		if not match_dirs or match_files:
			# Redirect match to fullmatch
			self.match = self.fullmatch
		return

	def __repr__(self):
		return 'pattern="%s", glob="%s", regex="%s"' % (self.match_pattern, self.globspec, self.regex)

	def match(self, src):
		return self.re.match(src)

	def fullmatch(self, src):
		return self.re.fullmatch(src)

class glob_expand:
	def __init__(self, expand_pattern, vars_dict={}):
		self.expand_pattern = expand_pattern
		self.expand_str = replace_vars_in_subst(expand_pattern, vars_dict)
		return

	def expand(self, match):
		try:
			return match.expand(self.expand_str)
		except re.error as ex:
			error_string = 'Error while mapping "%s"\n' % match.string
			error_string += 'Match pattern: "%s"\n' % self.match_pattern
			error_string += 'Replacement pattern: "%s" at pos %d\n' % (self.expand_pattern, ex.pos)

			raise Exception_cfg_parse(error_string)

### This class makes list of compiled regular expressions out of source string.
# By default, the souce string is split by ';'
# A segment without leading '!' makes a positive specification. If it matches,
# matching of the list will be stopped with result True. A leading '!' character
# can be escaped with a backslash.
# A segment starting with '!' makes a negative specification. If it matches,
# matching of the list will be stopped with result False
class path_list_match:
	def __init__(self, *values, vars_dict={}, match_dirs=False, match_files=False, split=';'):
		self.match_list = []
		self.match_dirs = match_dirs
		self.match_files = match_files

		return self.append(*values, vars_dict=vars_dict, split=split)

	def __repr__(self):
		return repr(self.match_list)

	def append(self, *values, vars_dict={}, split=';'):

		for src in values:
			for s in src.split(split):
				if not s:
					continue

				positive = True
				if s.startswith('!'):
					positive = False
					s = s[1:]
				elif s.startswith('\\!'):
					s = s[1:]

				self.match_list.append( (glob_match(s, vars_dict,
								match_dirs=self.match_dirs, match_files=self.match_files), positive) )
		return

	# If all match specifications are negative,
	# Or the list is empty, the function will return
	# 'return_for_no_positive'.
	# If a positive match was found, the function returns True
	# If a negative match was found, the function returns False
	# If the list contains positive matches , but none of them matches, the function returns None
	# If the list doesn't have any positive match, and none matches,
	# the function returns return_for_no_positive.
	# If you want an empty list to match all, pass return_for_no_positive=True
	def match(self, path, return_for_no_positive=None):
		for (m, positive) in self.match_list:
			if m.match(path):
				return positive
			if positive:
				return_for_no_positive = None
		# "Match not found" differs from "negative match found"
		return return_for_no_positive

	def fullmatch(self, path, return_for_no_positive=None):
		for (m, positive) in self.match_list:
			if m.fullmatch(path):
				return positive
			if positive:
				return_for_no_positive = None
		# "Match not found" differs from "negative match found"
		return return_for_no_positive

def bool_property_value(node, property_name, default=False):
	prop = node.get(property_name)
	if prop is None:
		return default

	prop = prop.lower()
	if prop == 'no' or prop == 'false' or prop == '0':
		return False
	if prop == 'yes' or prop == 'true' or prop == '1':
		return True
	ex = ValueError('ERROR: Invalid bool value %s="%s" in <%s> node' % (property_name, node.get(property_name), node.tag))
	ex.property_name = property_name
	ex.property_text = prop
	raise ex

def int_property_value(node, property_name, default=None, valid_range=None):
	value_text = node.get(property_name)
	if value_text is None:
		return default
	try:
		value = int(value_text)
	except ValueError:
		raise ValueError('ERROR: Invalid numeric value %s="%s" in <%s> node' % (property_name, value_text, node.tag))
	if valid_range and value not in valid_range:
		raise ValueError('ERROR: Numeric value %s="%s" in <%s> node not in the valid range %s'
			% (property_name, value_text, node.tag,
			('%d-%d' % (valid_range.start, valid_range.stop-1)) if type(valid_range) is range else str(valid_range)))

	return value

class path_map:
	def __init__(self, cfg, path, refname, alt_refname=None, revisions_ref=None):
		self.cfg = cfg
		self.path_match = glob_match(path, cfg.replacement_vars, match_dirs=True, match_files=False)

		if refname:
			self.refname_sub = glob_expand(refname, cfg.replacement_vars)

			if alt_refname:
				self.alt_refname_sub = glob_expand(alt_refname, cfg.replacement_vars)
			else:
				self.alt_refname_sub = None

			if revisions_ref:
				self.revs_ref_sub = glob_expand(revisions_ref, cfg.replacement_vars)
			else:
				self.revs_ref_sub = None
		else:
			self.refname_sub = None
			self.alt_refname_sub = None
			self.revs_ref_sub = None

		return

	def key(self):
		return self.path_match.regex

	def match(self, path):
		m = self.path_match.match(path)

		if not m:
			return None

		path = m[0]
		if path == '/':
			path = ''

		if not self.refname_sub:
			# This ref map suppresses creation of a branch
			return SimpleNamespace(
				path=path,
				globspec=self.path_match.globspec,
				refname=None,
				alt_refname=None,
				revisions_ref=None)

		refname = self.refname_sub.expand(m)

		if not refname.startswith('refs/'):
			refname = 'refs/' + refname

		if self.alt_refname_sub:
			alt_refname = self.alt_refname_sub.expand(m)
			if alt_refname and not alt_refname.startswith('refs/'):
				alt_refname = 'refs/' + alt_refname

		else:
			alt_refname = None

		if self.revs_ref_sub:
			revisions_ref = self.revs_ref_sub.expand(m)
			if revisions_ref and not revisions_ref.startswith('refs/'):
				revisions_ref = 'refs/' + revisions_ref
		else:
			revisions_ref = None

		return SimpleNamespace(
			path=path,
			globspec=self.path_match.globspec,
			refname=refname,
			alt_refname=alt_refname,
			revisions_ref=revisions_ref)

class project_config:
	def __init__(self, xml_node=None, filename=None):
		self.name = ""
		self.filename = filename

		## the map keeps regular expression replacements patterns
		self.map_set = set()
		self.map_list = []

		self.replacement_vars = {}
		self.replacement_chars = {}
		self.paths = path_list_match(match_dirs=True)
		self.chars_repl_re = None
		if xml_node:
			self.load(xml_node)
		return

	## Copies project configuration settings from XML element
	def load(self, xml_node):
		self.name = xml_node.get('Name', '')

		for node in xml_node.findall("./*"):
			tag = str(node.tag)
			if tag == 'Vars':
				self.add_vars_node(node)
			elif tag == 'MapPath':
				self.add_path_map_node(node)
			elif tag == 'Replace':
				self.add_char_replacement_node(node)
			elif node.get('FromDefault'):
				if node.get('FromDefault') == 'Yes':
					print("WARNING: Unrecognized tag <%s> in <Default>" % tag, file=sys.stderr)
					node.set('FromDefault', 'Muted')  # Do not issue the warning again
				pass
			else:
				print("WARNING: Unrecognized tag <%s> in <Project Name=\"%s\">" % (tag, self.name), file=sys.stderr)

		self.paths.append(xml_node.get('Path', self.name), vars_dict=self.replacement_vars)

		self.make_chars_replacement_regex()
		return

	## Add regex patterns and substitutions to convert SVN paths
	# to refnames.
	# An asterisk matches any path component (a stting without path separator '/'
	# A double asterisk matches any number of path components -
	# any string with any number of path separators.
	# $n are replacement strings, for the corresponding wildcards.
	#	<MapPath>
	#		<Path>**/$Trunk</Path>
	#		<Refname>refs/heads/$1/$MapTrunkTo</Refname>
	#		<!--
	#		$RevisionRef/r<n> ref will be created when a revision results in a commit.
	#		-->
	#		<RevisionRef>refs/revisions/$1/$MapTrunkTo</RevisionRef>
	#	</MapPath>
	#	<MapPath>
	#		<Path>**/$UserBranches/*/*</Path>
	#		<Refname>refs/heads/$1/users/$2/$3</Refname>
	#		<RevisionRef>refs/revisions/$1/users/$2/$3</RevisionRef>
	#	</MapPath>

	def add_vars_node(self, node):
		for vnode in node.findall("./*"):
			if vnode.tag:
				self.add_replacement_var(vnode.tag, vnode.text)
		return

	def add_replacement_var(self, var, text):
		if text is None:
			self.replacement_vars.pop(var, '')
			return
		t = str(text).strip()
		# strings with whitespaces and characters not allowed in refs are ignored with a warnind
		m = re.search('( |\t|\r|\n|\^|\?|\[|\*|%)', t)
		if m:
			# FIXME: Spaces are allowed in paths
			print("WARNING: Variable %s value contains invalid character '%s'" % (var, re.escape(m[0])), file=sys.stderr)
			return
		self.replacement_vars[var] = t.split(';')
		return

	def add_path_map_node(self, path_map_node):

		node = path_map_node.find("./Path")
		if node is None:
			raise Exception_cfg_parse("Missing <Path> node in <MapPath>")

		path = node.text
		if not path:
			raise Exception_cfg_parse("Missing directory pattern in <MapPath><Path> node")

		node = path_map_node.find("./Refname")
		if node is not None:
			refname = node.text
		else:
			refname = None

		if refname:
			node = path_map_node.find("./AltRefname")
			if node is not None and node.text:
				alt_refname = node.text
			else:
				alt_refname = None

			node = path_map_node.find("./RevisionRef")
			if node is not None and node.text:
				revs_ref = node.text
			else:
				revs_ref = None
		else:
			alt_refname = None
			revs_ref = None

		new_map = path_map(self, path, refname, alt_refname, revs_ref)

		if new_map.key() in self.map_set:
			if path_map_node.get('FromDefault') is None:
				raise Exception_cfg_parse("Directory mapping for '%s' specified twice in the config" % new_map.path_match.globspec)
			# Ignore duplicate mapping from <Default>
			return

		self.map_set.add(new_map.key())
		self.map_list.append(new_map)

		return

	def add_char_replacement_node(self, node):
		chars_node = node.find("./Chars")
		with_node = node.find("./With")

		if chars_node is not None and chars_node.text and with_node is not None and with_node.text is not None:
			self.replacement_chars[chars_node.text] = with_node.text
		return

	def make_chars_replacement_regex(self):
		chars_list = self.replacement_chars.keys()
		if chars_list:
			self.chars_repl_re = re.compile('|'.join([re.escape(s) for s in chars_list]))

	def apply_char_replacement(self, ref):
		if not self.chars_repl_re:
			return ref

		return self.chars_repl_re.sub(lambda m : self.replacement_chars.get(m.group(0), ''), ref)

	## The function finds a branch map for an SVN path
	# @param path - path in the repository.
	# If found, it returns a path_map object
	# If a branch map not found, return None
	def map_path(self, path):
		if not self.paths.match(path, True):
			return None

		# The match patterns are made in order of their appearance in XML config Project section
		for path_map in self.map_list:
			branch_map = path_map.match(path)
			if branch_map is not None:
				return branch_map

		return None

	## merge_cfg_nodes combines two ET.Element nodes
	# into a new node
	@staticmethod
	def merge_cfg_nodes(cfg_node, default_node):
		if cfg_node is None:
			cfg_node = default_node
			default_node = None
		merged = ET.Element(cfg_node.tag, cfg_node.attrib.copy())
		merged.extend(cfg_node.findall("./*"))
		if not default_node:
			return merged

		idx = 0
		for node in default_node.findall("./*"):
			if node.tag == 'Vars' or \
					node.tag == 'Replace':
				# Vars and replacement from default config are assigned first to be overwritten by later override
				merged.insert(idx, node)
				idx += 1
			elif node.tag == 'MapPath' or \
					cfg_node.find("./" + node.tag) is None:
				# Map from default config is assigned last to be processed after non-default
				merged.append(node)
			node.attrib.setdefault('FromDefault', 'Yes')
		return merged

	@staticmethod
	def make_default_config():
		default_cfg = """<Default>
	<Vars>
		<Trunk>trunk</Trunk>
		<Branches>branches</Branches>
		<UserBranches>users/branches;branches/users</UserBranches>
		<Tags>tags</Tags>
		<MapTrunkTo>master</MapTrunkTo>
	</Vars>
	<MapPath>
		<Path>**/$UserBranches/*/*</Path>
		<Refname>refs/heads/**/users/*/*</Refname>
	</MapPath>
	<MapPath>
		<Path>**/$UserBranches/*</Path>
		<Refname />
	</MapPath>
	<MapPath>
		<Path>**/$Branches/*</Path>
		<Refname>refs/heads/**/*</Refname>
	</MapPath>
	<MapPath>
		<Path>**/$Tags/*</Path>
		<Refname>refs/tags/**/*</Refname>
		<AltRefname>refs/heads/**/tags/*</AltRefname>
	</MapPath>
	<MapPath>
		<Path>**/$Trunk</Path>
		<Refname>refs/heads/**/$MapTrunkTo</Refname>
	</MapPath>
	<Replace>
		<Chars> </Chars>
		<With>_</With>
	</Replace>
	<Replace>
		<Chars>:</Chars>
		<With>.</With>
	</Replace>
	<Replace>
		<Chars>^</Chars>
		<With>+</With>
	</Replace>
</Default>"""
		return default_cfg

	@staticmethod
	def make_config_list(xml_filename, default_cfg=None):
		# build projects directory

		if default_cfg is not None:
			default_cfg = ET.XML(default_cfg)

		configs = set()
		config_list = []

		if xml_filename:
			try:
				xml_cfg = ET.parse(xml_filename)
			except ET.ParseError as ex:
				raise Exception_cfg_parse("In XML config file %s: %s" % (xml_filename, ex))

			root = xml_cfg.getroot()
			if root.tag != "Projects":
				raise Exception_cfg_parse("XML config: root tree must be <Projects>")

			default_cfg = project_config.merge_cfg_nodes(root.find("./Default"), default_cfg)

			for cfg_node in root.findall("./Project"):
				cfg = project_config(project_config.merge_cfg_nodes(cfg_node, default_cfg), filename = xml_filename)

				if cfg.name in configs:
					raise Exception_cfg_parse("XML config: <Project Name=\"%s\" encountered twice" % cfg.name)
				configs.add(cfg.name)
				config_list.append(cfg)

		if not config_list:
			# XML config not specified, use default configuration
			config_list.append(project_config(default_cfg, xml_filename))

		return config_list
