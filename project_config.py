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
from rev_ranges import str_to_ranges

# Variables can be replaced in:
# 1. Glob strings (paths and refs)
# 2. Replacement ref strings.
# Replacement can be recursive, but same variable cannot be replaced again in a nested replacement.
# Replacement generates a string of tokens.
# In a glob string, slashes are generated as separate tokens.
#   Multiple choice match in braces {A,B,C} is also allowed in the primary string.
#   Multiple choice is not allowed during recursive substitution of variables.
# In a replacement string, tokenization only goes up to variables

class glob_string_token:
	def __init__(self, text='', capture=False):
		self.text = text
		self.capture = capture
		return

	def regex(self):
		return re.escape(self.text)

	def globspec(self):
		return self.text

	def expand_str(self):
		return self.text

	def contains_slash(self):
		return False

	def add_to_token_list(self, token_list, tokens_iter):
		token_list.append(self)
		return

	def adjust_expand_str(self, wildcards, tokens_iter, last_var):
		#nothing
		return last_var

	def __repr__(self):
		return repr(self.text)

class wildcard_token(glob_string_token):
	def __init__(self, text, capture):
		super().__init__(text, capture)
		self.var = None
		return

	def regex(self):
		if self.capture:
			prefix = '('
		else:
			prefix = '(?:'

		if self.text == '?':
			return prefix + '[^/])'
		elif self.text == '*':
			return prefix + '[^/]*)'
		elif self.text == '*/':
			return prefix + '[^/]+)/'
		elif self.text == '**/':
			return prefix + '.*/)?'
		elif self.text == '/**?':
			return prefix + '/.*)?'
		elif self.text == '/**':
			return prefix + '/.*)'
		else:
			assert(self.text == '**')
			return prefix + '.*)'

	def add_to_token_list(self, token_list, tokens_iter):
		prev_token = token_list.get(-1)
		if prev_token and type(prev_token) is not slash_token:
			return token_list.append(self)

		next_token = next(tokens_iter, None)
		if type(next_token) is slash_token:
			if self.text == '*':
				next_token.text = ''
				self.text = '*/'
			elif self.text.startswith('**'):
				next_token.text = ''
				self.text = '**/'
		token_list.append(self)
		if next_token:
			return next_token.add_to_token_list(token_list, tokens_iter)

		return

	def contains_slash(self):
		return self.text.startswith('**')

	def expand_str(self):
		if self.var is None:
			raise Exception_cfg_parse("Encountered a wildcard '%s' in the substitution string" % self.text)
		return self.var

	def adjust_expand_str(self, wildcards, tokens_iter, last_var):
		last_var += 1
		# Use this specification instead of simple \NN to avoid ambiguity
		self.var = r'\g<' + str(last_var) + '>'
		if self.text == '*/':
			self.var += '/'
		return last_var

	def __repr__(self):
		return 'Wildcard:' + self.text

class list_token(glob_string_token):
	# The token is inserted for a replacement of a variable with a list of values
	def __init__(self, values, var, token_list_list, text='', capture=False):
		super().__init__(text, capture)
		# values is the original list of the variable value
		self.values = values
		# values is a list of lists of tokens, each generated by tokenize_glob_string
		self.token_list_list = token_list_list
		# Original variable being substituted
		self.var = var
		if capture:
			self.regex_prefix = '('
		else:
			self.regex_prefix = '(?:'
		return

	def regex(self):
		return self.regex_prefix + '|'.join(token_list.regex() for token_list in self.token_list_list) + ')'

	def globspec(self):
		return '{' + ','.join(token_list.globspec() for token_list in self.token_list_list) + '}'

	def expand_str(self):
		raise Exception_cfg_parse("Encountered a list variable $%s in the substitution string" % self.var)

	def contains_slash(self):
		for token_list in self.token_list_list:
			if token_list.has_slashes():
				return True
		return False

	def __repr__(self):
		return 'List var $%s:%s' % (self.var, self.values)

class brace_group_token(list_token):
	def __init__(self, text, capture):
		super().__init__(None, None, [wildcard_token_list('')], text, capture)
		return

	def expand_str(self):
		raise Exception_cfg_parse("Encountered a brace group expression {} in the substitution string")

	def add_to_token_list(self, token_list, tokens_iter):
		if self.text != '{':
			raise Exception_cfg_parse(
				"'%s' outside of brace group specification" % self.text)

		prev_capture = token_list.capture
		token_list.capture = False
		while (token := next(tokens_iter, None)) is not None:
			if type(token) is brace_group_token:
				if token.text == '}':
					break
				if token.text == ',':
					self.token_list_list.append(wildcard_token_list(''))
					continue

			token.add_to_token_list(self.token_list_list[-1], tokens_iter)
		else:
			# Group is not closed, error
			raise Exception_cfg_parse('Brace group specification is not closed')

		token_list.capture = prev_capture
		token_list.append(self)
		return

	def __repr__(self):
		return 'Match group:'+repr(self.globspec())

class text_token(glob_string_token):

	def add_to_token_list(self, token_list, tokens_iter):

		last_token = token_list.get(-1)
		if type(last_token) is text_token:
			last_token.text += self.text
		else:
			token_list.append(self)
		return

class slash_token(glob_string_token):

	def regex(self):
		# the text can be reset to empty string
		return self.text

	def contains_slash(self):
		return True

	def add_to_token_list(self, token_list, tokens_iter):
		# Drop duplicated slashes
		if type(token_list.get(-1)) is not slash_token:
			token_list.append(self)
		return

class subst_token(glob_string_token):
	def __init__(self, text, var):
		super().__init__(text)
		self.var = var
		return

	def regex(self):
		raise Exception_cfg_parse('Encountered a substitution spec %s in the pattern match string' % self.text)

	def globspec(self):
		raise Exception_cfg_parse('Encountered a substitution spec %s in the pattern match string' % self.text)

	def expand_str(self):
		# Use this specification instead of simple \NN to avoid ambiguity
		return r'\g<' + self.var + '>'

	def adjust_expand_str(self, wildcards, tokens_iter, last_var):
		last_var = int(self.var)
		if last_var > len(wildcards):
			raise Exception_cfg_parse('Substitution spec "%s" outside of wildcards count (%d)'
										% (self.text, len(wildcards)))
		# Check if it's followed by a slash:
		if wildcards[last_var - 1].text != '**/':
			return last_var

		next_token = next(tokens_iter, None)
		if next_token is None:
			return last_var
		if type(next_token) is slash_token:
			next_token.text = ''
		return next_token.adjust_expand_str(wildcards, tokens_iter, last_var)

	def __repr__(self):
		return 'Subst:'+self.text

# Only the top level specification makes into a replacement
# If we're making path glob, we'll follow Git .gitignore rules

# To make a regex for a path, we split the list to slashes, and other nested lists:
# The resulting path_components contains strings or lists of fragments or strings. It's assumed there's a slash between each item of the list.
# If an item is an empty list, this means there's a slash in the beginning or in the end.
# If an item is a list of a single simple string, it's a complete path component.
# Otherwise, the item a list of wildcard and grouping components
class wildcard_token_list:
	# The string is split by slashes, double dollar characters
	# (which are replaced by a single character) and variables in braces
	# and not in braces.
	# Note that (?![0-9]) prevents variable names starting from a number from matching
	# The numeric variable names are matched by separate groups 3 and 4
	tokenize_regex = re.compile(r'/|\$\$|\\\$|\\[{},]|\\|\$\{(?![0-9])(\w+)\}|\$(?![0-9])(\w+)|\${([1-9][0-9]*)}|\$([1-9][0-9]*)|(\*+|\?)|([{},])')

	def tokenizer(self, src, vars_dict):
		prev_end = 0
		if not src:
			return

		for m in self.tokenize_regex.finditer(src):
			start, end = m.span()
			if prev_end != start:
				# Return the string between matches, except for empty string
				yield text_token(src[prev_end:start])
			prev_end = end

			token = m[0]
			if token == '\\':
				raise Exception_cfg_parse('Encountered a misplaced backslash character')

			if token == '/':
				yield slash_token(token)
				continue

			if token == '$$' or token[0] == '\\':
				yield text_token(token[1:])
				continue

			var = m[3]
			if not var:
				var = m[4]
			if var:
				yield subst_token(token, var)
				continue

			var = m[5]
			if var:
				yield wildcard_token(var, self.capture)
				continue

			var = m[6]
			if var:
				yield brace_group_token(var, self.capture)
				continue

			var = m[1]
			if not var:
				var = m[2]
			if not var:
				yield text_token(token)
				continue

			value = vars_dict[var]	# will raise KeyError if not in dictionary

			# Perform recursive replacement, but make sure there's no cycle
			new_vars_dict = vars_dict.copy()
			# Delete the name from the copy of the dictionary after having referred
			del new_vars_dict[var]
			# Variable can be a list value
			if len(value) > 1:
				yield list_token(value, var,
					list(wildcard_token_list(item, new_vars_dict) for item in value))
			else:
				yield from self.tokenizer(value[0], new_vars_dict)
			continue

		if prev_end != len(src):
			# Return the string between matches, except for empty string
			yield text_token(src[prev_end:])

		return

	def __init__(self, src, vars_dict={}, capture=False):
		self.tokens = []
		self.capture = capture
		tokens_iter = self.tokenizer(src, vars_dict)

		for token in tokens_iter:
			token.add_to_token_list(self, tokens_iter)

		return

	def get(self, index):
		if index >= len(self.tokens) \
				or index < -len(self.tokens):
			return None
		return self.tokens[index]

	def append(self, token):
		return self.tokens.append(token)

	def has_slashes(self, tokens=None):
		# A path component may have embedded slashes if any of its group matches
		# has embedded slashes,
		# Or it contains a '** wildcard
		if tokens is None:
			tokens = self.tokens
		for token in tokens:
			if token.contains_slash():
				return True

		return False

	def regex(self, tokens=None):

		if not tokens:
			tokens = self.tokens

		return ''.join(token.regex() for token in tokens)

	def adjust(self, match_dirs, match_files):
		if not match_dirs and not match_files:
			# Literal match as is without any adjustment
			return self.tokens

		tokens = self.tokens.copy()
		last_token = self.get(-1)
		ends_with_slash = type(last_token) is slash_token

		if match_dirs and not match_files and not ends_with_slash:
			# To properly match directories, make sure there's a trailing slash:
			if type(last_token) is wildcard_token \
					and last_token.text == '*' \
					and type(self.get(-2)) is slash_token:
				# the spec ended with /*
				last_token.text = '*/'
				tokens.append(slash_token(''))
			else:
				tokens.append(slash_token('/'))
			ends_with_slash = True
		# else this function makes a regex to match filenames AND directories,
		# which should go to re.fullmatch

		list_len = len(tokens)
		# Adjust the tokens for special cases:
		# 1. The single list: add "any" prefix
		# 2. The list equivalent to dir/ - non-empty list and empty list
		#  - add "any" prefix, only if the list doesn't contain ** wildcard or
		#   nested directory separators
		#  Otherwise remove any leading backslash (empty list) if present
		if list_len > 1 and type(tokens[0]) is slash_token:
			# Remove the first slash. The spec always matches from the beginning of path
			tokens.pop(0)
		elif (list_len == 1 or not ends_with_slash) \
				and self.has_slashes(tokens):
			# A slash is present somewhere in the middle
			pass
		elif list_len > 1 and ends_with_slash \
				and self.has_slashes(tokens[:-1]):
			pass
		else:
			# prepend "any" prefix, but only if there's no directory separators in the nested lists
			# If non-capturing list is */, replace it with **/,
			# otherwise just prepend non-capturing **/
			if list_len == 2 \
					and tokens[0].text == '*' \
					and not tokens[0].capture \
					and ends_with_slash:
				# The globspec is */ non-capturing
				tokens = []

			# The globspec is either 'xxx' or 'xxx/'
			tokens = [wildcard_token('**/', capture=False), slash_token('')] + tokens

		if ends_with_slash:
			if match_files:
				# This regex needs to match files AND directories, and will be used with fullmatch()
				tokens.append(wildcard_token('**', capture=False))
		elif match_dirs and match_files:
			# The last component of this regex needs to match files AND directories, and will be used with fullmatch()
			tokens += [slash_token(''), wildcard_token('/**?', capture=False)]

		return tokens

	def globspec(self):
		return ''.join(token.globspec() for token in self.tokens)

	def expand_str(self, wildcards=None):
		tokens = self.tokens
		if wildcards is not None:
			tokens = tokens.copy()
			# Check the substitutions against the match string
			# **/ must match **/
			# Make an array of wildcard objects

			last_var = 0
			tokens_iter = iter(tokens)
			for token in tokens_iter:
				last_var = token.adjust_expand_str(wildcards, tokens_iter, last_var)

		return ''.join(token.expand_str() for token in tokens)

	def get_capture_list(self):
		wildcards = []
		for token in self.tokens:
			if token.capture:
				wildcards.append(token)
		return wildcards

class wildcard_parser:
	def __init__(self, src, vars_dict={}, capture=False):
		try:
			self.token_list = wildcard_token_list(src, vars_dict, capture)

		except KeyError as ex:
			key = ex.args[0]
			if key not in vars_dict:
				raise Exception_cfg_parse(
					'Replacement in "%s": Variable $%s not defined in any <Vars> specification:'
					% (src, key))
			else:
				raise Exception_cfg_parse(
					'Replacement in "%s": Variable $%s recursively referred'
					% (src, key))

		return

	def globspec(self):
		return self.token_list.globspec()

	def expand_str(self, wildcards=None):
		return self.token_list.expand_str(wildcards)

	def regex(self, match_dirs, match_files):
		tokens = self.token_list.adjust(match_dirs, match_files)
		return self.token_list.regex(tokens)

	def get_capture_list(self):
		return self.token_list.get_capture_list()

class glob_match:
	def __init__(self, match_pattern, vars_dict={}, match_dirs=False, match_files=False, capture=False):
		self.match_pattern = match_pattern

		parser = wildcard_parser(match_pattern, vars_dict, capture=capture)

		self.regex = parser.regex(match_dirs=match_dirs, match_files=match_files)
		self.re = re.compile(self.regex)
		self.globspec = parser.globspec()
		if match_dirs and not match_files and not self.globspec.endswith('/'):
			self.globspec += '/'

		self.wildcards = parser.get_capture_list()

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

	def get_capture_list(self):
		return self.wildcards

class glob_expand:
	def __init__(self, expand_pattern, vars_dict={}, glob_match = None):
		self.expand_pattern = expand_pattern

		if glob_match is not None:
			wildcards = glob_match.get_capture_list()
		else:
			wildcards = None
		self.expand_str = wildcard_parser(expand_pattern, vars_dict).expand_str(wildcards)
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
	def __init__(self, cfg, path, refname, alt_refname=None, revisions_ref=None, block_upper_level=True):
		self.cfg = cfg
		self.path_match = glob_match(path, cfg.replacement_vars, match_dirs=True, match_files=False, capture=True)

		if refname:
			self.refname_sub = glob_expand(refname, cfg.replacement_vars, self.path_match)

			if alt_refname:
				self.alt_refname_sub = glob_expand(alt_refname, cfg.replacement_vars, self.path_match)
			else:
				self.alt_refname_sub = None

			if revisions_ref:
				self.revs_ref_sub = glob_expand(revisions_ref, cfg.replacement_vars, self.path_match)
			else:
				self.revs_ref_sub = None
		else:
			self.refname_sub = None
			self.alt_refname_sub = None
			self.revs_ref_sub = None

		self.edit_msg_list = []
		self.inherit_mergeinfo = False

		if block_upper_level:
			# If the (expanded) path pattern has /* or /** specifications at the end,
			# We need to match the upper directory without those wildcards
			# to block it from creating branches.
			# Such regular expression would end in a number of
			# '/([^/]+)' strings
			match = re.fullmatch(r'(.*?)(%s)+/?' % re.escape('/([^/]+)'), self.path_match.regex)
			if match:
				# Since the wildcard matches all subdirectories, add an exclusion map for the
				# enclosing directory:
				self.block_upper_level_regex = re.compile(match[1] + '/')
				return

		self.block_upper_level_regex = None
		return

	def key(self):
		return self.path_match.regex

	def match(self, path):
		m = self.path_match.match(path)

		if not m:
			if self.block_upper_level_regex:
				m = self.block_upper_level_regex.match(path)
				if m:
					# This path matches a regular expression which blocks the upper level path
					# from creating a branch.
					return SimpleNamespace(
						path=m[0],
						globspec=self.path_match.globspec,
						refname=None,
						alt_refname=None,
						revisions_ref=None)
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
			edit_msg_list=self.edit_msg_list,
			inherit_mergeinfo=self.inherit_mergeinfo,
			revisions_ref=revisions_ref)

class project_config:
	def __init__(self, xml_node=None, filename=None):
		self.name = ""
		self.filename = filename

		## the map keeps regular expression replacements patterns
		self.map_set = set()
		self.map_list = []
		self.ref_map_set = set()
		self.ref_map_list = []

		self.replacement_vars = {}
		self.replacement_chars = {}
		self.gitattributes = []
		self.paths = path_list_match(match_dirs=True)
		self.edit_msg_list = []
		self.chars_repl_re = None
		self.explicit_only = False
		self.needs_configs = ""
		self.inherit_mergeinfo = False
		if xml_node:
			self.load(xml_node)
		return

	## Copies project configuration settings from XML element
	def load(self, xml_node):
		# <Project ExplicitOnly=Yes"> sections are only used when explicitly selected
		# If no --project option in the command line, or all patterns are negative,
		# <Project ExplicitOnly=Yes"> sections are not used
		self.explicit_only = bool_property_value(xml_node, "ExplicitOnly", False)
		self.needs_configs = xml_node.get("NeedsProjects", "")
		self.inherit_mergeinfo = bool_property_value(xml_node, 'InheritMergeinfo', True)

		self.name = xml_node.get('Name', '')

		for node in xml_node.findall("./*"):
			tag = str(node.tag)
			if tag == 'Vars':
				self.add_vars_node(node)
			elif tag == 'MapPath':
				self.add_path_map_node(node)
			elif tag == 'UnmapPath':
				self.add_path_unmap_node(node)
			elif tag == 'Replace':
				self.add_char_replacement_node(node)
			elif tag == 'MapRef':
				self.add_ref_map_node(node)
			elif tag == 'EditMsg':
				self.edit_msg_list.append(self.process_edit_msg_node(node))
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

		if not refname:
			raise Exception_cfg_parse("Directory mapping for '%s' is missing <Refname> specification. Use <UnmapPath> instead." % path)

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

		new_map = path_map(self, path, refname, alt_refname, revs_ref,
				 block_upper_level=bool_property_value(path_map_node, 'BlockParent',True))

		if new_map.key() in self.map_set:
			if path_map_node.get('FromDefault') is None:
				raise Exception_cfg_parse("Directory mapping for '%s' specified twice in the config" % new_map.path_match.globspec)
			# Ignore duplicate mapping from <Default>
			return

		for node in path_map_node.findall("./EditMsg"):
			new_map.edit_msg_list.append(self.process_edit_msg_node(node))

		new_map.inherit_mergeinfo = bool_property_value(path_map_node, 'InheritMergeinfo', self.inherit_mergeinfo)

		self.map_set.add(new_map.key())
		self.map_list.append(new_map)

		return

	def add_path_unmap_node(self, path_unmap_node):

		path = path_unmap_node.text
		if not path:
			raise Exception_cfg_parse("Missing directory pattern in <UnmapPath> node")

		# Replace $name strings:
		# and create a regex string from path string:
		unmap = path_map(self, path, None, None, None,
				 block_upper_level=bool_property_value(path_unmap_node, 'BlockParent',True))

		if unmap.key() in self.map_set:
			if path_unmap_node.get('FromDefault') is None:
				# Ignore duplicate mapping from <Default>
				print("WARNING: <UnmapPath> for path '%s' already matched in the config" % unmap.path_match.globspec)
			return

		if path_unmap_node.findall("./*"):
			print ("WARNING: Subnodes under <UnmapPath>%s</UnmapPath> are ignored" % (path))

		self.map_set.add(unmap.key())
		self.map_list.append(unmap)

		return

	def add_ref_map_node(self, ref_map_node):

		node = ref_map_node.find("./Ref")
		if node is None:
			raise Exception_cfg_parse("Missing <Ref> node in <MapRef>")

		ref = node.text
		if not ref:
			raise Exception_cfg_parse("Missing ref pattern in <MapRef><Ref> node")

		# Replace $name strings:
		# and create a regex string from path string:
		refname = glob_match(ref, self.replacement_vars, match_dirs=False, match_files=True, capture=True)

		if refname.regex in self.ref_map_set:
			if ref_map_node.get('FromDefault') is None:
				raise Exception_cfg_parse("Ref mapping for '%s' specified twice in the config" % refname.globspec)
			# Ignore duplicate mapping from <Default>
			return

		node = ref_map_node.find("./NewRef")
		if node is not None:
			new_refname = node.text
		else:
			new_refname = None

		if new_refname:
			new_refname = glob_expand(new_refname, self.replacement_vars, refname)

		ref_map = SimpleNamespace(refname=refname,expand_refname=new_refname)

		self.ref_map_set.add(refname.regex)
		self.ref_map_list.append(ref_map)
		return

	def process_edit_msg_node(self, edit_msg_node):
		# attributes: Revs="revision ranges" Branch="branch match" Max="max substitutions" Final="True"
		revs = edit_msg_node.get('Revs', '')
		try:
			revs = str_to_ranges(revs)
		except ValueError:
			raise Exception_cfg_parse(
				'Invalid Revs specification "%s" in <EditMsg> node')

		branch = edit_msg_node.get('Branch', '*')
		branch = glob_match(branch, self.replacement_vars, match_dirs=True, match_files=True)

		max_sub = int_property_value(edit_msg_node, 'Max', 0)
		final = bool_property_value(edit_msg_node, 'Final', False)
		match_node = edit_msg_node.find('./Match')
		if match_node is None or \
			not (match := match_node.text):
			match = '.*'
			max_sub = 1
			final = True
		try:
			match_re = re.compile(match, re.MULTILINE)
		except re.error as e:
			raise Exception_cfg_parse(
				'Invalid regular expression "%s" as match pattern in <EditMsg><Match> node:\n\t%s' % (match, e.msg))

		# <Replace>substitution</Replace>
		replace_node = edit_msg_node.find('./Replace')
		if replace_node is None:
			raise Exception_cfg_parse("Missing <Replace> node in <EditMsg>")
		replace = replace_node.text
		if replace is None:
			# Empty text is returned as None
			replace = ''

		return SimpleNamespace(
				match=match_re,
				replace=replace,
				revs=revs,
				branch=branch,
				max_sub=max_sub,
				final=final)

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

	def map_ref(self, ref):
		if not ref:
			return ref

		# Apply MapRef translation patterns.
		for ref_map in self.ref_map_list:
			m = ref_map.refname.fullmatch(ref)
			if m:
				if not ref_map.expand_refname:
					return None
				ref = ref_map.expand_refname.expand(m)
				break
			continue

		return self.apply_char_replacement(ref)

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

		inherit_default = bool_property_value(cfg_node, "InheritDefault", True)
		inherit_default_mapping = bool_property_value(cfg_node, "InheritDefaultMapping", inherit_default)

		idx = 0
		for node in default_node.findall("./*"):
			if node.tag == 'MapPath' or \
					node.tag == 'UnmapPath':
				if not inherit_default_mapping:
					continue
				# Map from default config is assigned last to be processed after non-default
				merged.append(node)
				node.attrib.setdefault('FromDefault', 'Yes')
				continue
			elif node.tag == 'Vars' or node.tag == 'Replace':
				# Vars and Replace are always inherited from the hardcoded default
				if not inherit_default and node.get('HardcodedDefault') != 'Yes':
					continue
				# These specifications from the default config are assigned first to be overwritten by later override
				merged.insert(idx, node)
				idx += 1
				continue

			if not inherit_default:
				continue
			if node.tag == 'MapRef' or \
					node.tag == 'EditMsg' or \
					cfg_node.find("./" + node.tag) is None:
				# The rest of tags are not taken as overrides. They are only appended
				# if not already present in this config
				# And these specifications from the default config are assigned last to be processed after non-default
				merged.append(node)
				node.attrib.setdefault('FromDefault', 'Yes')
		return merged

	@staticmethod
	def make_default_config(options=None):
		vars_section = '''
		<Trunk>%s</Trunk>
		<Branches>%s</Branches>
		<Tags>%s</Tags>
		<MapTrunkTo>%s</MapTrunkTo>''' % (
				getattr(options, 'trunk', 'trunk'),
				getattr(options, 'branches', 'branches'),
				getattr(options, 'tags', 'tags'),
				getattr(options, 'map_trunk_to', 'main'))

		user_branches = ';'.join(getattr(options, 'user_branches', ['users/branches', 'branches/users']))
		if user_branches:
			vars_section += '\n\t\t<UserBranches>%s</UserBranches>' % user_branches
			user_branch_mappings = '''
	<MapPath>
		<Path>**/$UserBranches/*/*</Path>
		<Refname>refs/heads/**/users/*/*</Refname>
	</MapPath>'''
		else:
			user_branch_mappings = ''

		if getattr(options, 'use_default_config', True):
			default_mappings = user_branch_mappings + '''
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
	</MapPath>'''
		else:
			default_mappings = ''

		default_cfg = """<Default>
	<Vars HardcodedDefault="Yes">%s
	</Vars>%s
	<Replace HardcodedDefault="Yes">
		<Chars> </Chars>
		<With>_</With>
	</Replace>
	<Replace HardcodedDefault="Yes">
		<Chars>:</Chars>
		<With>.</With>
	</Replace>
	<Replace HardcodedDefault="Yes">
		<Chars>^</Chars>
		<With>+</With>
	</Replace>
</Default>""" % (vars_section, default_mappings)
		return default_cfg

	@staticmethod
	def make_config_list(xml_filename, project_filters=[], default_cfg=None):
		# build projects directory

		if default_cfg is not None:
			default_cfg = ET.XML(default_cfg)

		configs = set()
		config_list = []
		# A fallback config is the first config with empty name or name='*'
		fallback_config = None

		if xml_filename:
			try:
				xml_cfg = ET.parse(xml_filename)
			except ET.ParseError as ex:
				raise Exception_cfg_parse("In XML config file %s: %s" % (xml_filename, ex))

			root = xml_cfg.getroot()
			if root.tag != "Projects":
				raise Exception_cfg_parse("XML config: root tree must be <Projects>")

			# match_dirs=False, match_files=False means literal match
			project_filter_list = path_list_match(*project_filters, split=',')
			all_config_list = []

			default_cfg = project_config.merge_cfg_nodes(root.find("./Default"), default_cfg)

			project_nodes = root.findall("./Project")
			for cfg_node in project_nodes:

				cfg = project_config(project_config.merge_cfg_nodes(cfg_node, default_cfg), filename = xml_filename)

				if cfg.name in configs:
					raise Exception_cfg_parse("XML config: <Project Name=\"%s\" encountered twice" % cfg.name)
				configs.add(cfg.name)
				all_config_list.append(cfg)

			need_projects = set()
			selected_config_list = []
			config_list_chaged= True
			while config_list_chaged:
				config_list = []
				config_list_chaged = False
				for cfg in all_config_list:
					if selected_config_list and cfg is selected_config_list[0]:
						selected_config_list.pop(0)
						config_list.append(cfg)
						continue

					if not cfg.explicit_only \
							and fallback_config is None \
							and (not cfg.name or cfg.name == '*'):
						fallback_config = cfg

					if not project_filter_list.match(cfg.name, not cfg.explicit_only) \
						and not cfg.name in need_projects:
						continue

					for needs_name in cfg.needs_configs.split(','):
						if needs_name:
							need_projects.add(needs_name)

					config_list_chaged= True
					config_list.append(cfg)
				if not need_projects:
					break
				selected_config_list = config_list
				continue

			need_projects -= configs
			if need_projects:
				print('WARNING: Projects with names "%s" referred by NeedsProjects attributes not present'
						% ','.join(need_projects), file=sys.stderr)

			if config_list:
				return config_list

			if not project_nodes:
				fallback_config = project_config(default_cfg, xml_filename)
				print('WARNING: XML config: No section <Project> present; using <Default> config')
			elif fallback_config is None:
				raise Exception_cfg_parse('XML config: No <Project> node found for the specified --project option, and no section <Project Name="*"> present')
			else:
				print('WARNING: XML config: No <Project> node found for the specified --project option; using a section <Project Name="*">')
		else:
			fallback_config = project_config(default_cfg, xml_filename)

		# No <Project> node specified, use default configuration
		# Add project name specifications as path filters
		fallback_config.paths.append(*project_filters)

		return [fallback_config]
