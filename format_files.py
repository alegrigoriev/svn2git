#!/bin/env python3

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
from typing import Generator
import sys
import io, os
from types import SimpleNamespace
# Indent detection

from pathlib import Path
import re

SPACE=ord(b' ')
TAB=ord(b'\t')
EOL=b'\n'
CR=ord(b'\r')
SLASH=ord(b'/')
ASTERISK=ord(b'*')
POUND=ord(b'#')
BRACE_OPEN=ord(b'{')
BRACE_CLOSE=ord(b'}')
PAREN_OPEN=ord(b'(')
PAREN_CLOSE=ord(b')')
SINGLE_QUOTE=ord(b"'")
DOUBLE_QUOTE=ord(b'"')
WIDE_STRING_PREFIX=ord(b'L')
BACKSLASH=ord(b'\\')
SEMICOLON=ord(b';')
COLON=ord(b':')
QUESTION=ord(b'?')
COMMA=ord(b',')

LINE_INDENT_KEEP_CURRENT = -1
LINE_INDENT_KEEP_CURRENT_NO_RETAB = -2

UPPERCASE_A=ord(b'A')
LOWERCASE_a=ord(b'a')
UPPERCASE_Z=ord(b'Z')
LOWERCASE_z=ord(b'z')
NUMBER_0=ord(b'0')
NUMBER_9=ord(b'9')
UNDERSCORE=ord(b'_')
DOLLAR_SIGN=ord(b'$')
AT_SIGN=ord(b'@')

def is_alphanumeric(c):
	if type(c) is not int:
		return False
	return (c >= UPPERCASE_A and c <= UPPERCASE_Z) \
		or (c >= LOWERCASE_a and c <= LOWERCASE_z) \
		or (c >= NUMBER_0 and c <= NUMBER_9) \
		or c == UNDERSCORE \
		or c == DOLLAR_SIGN \
		or c == AT_SIGN

def format_err_handler(s):
	raise BaseException(s)

class parse_line:
	def __init__(self, line, config):
		self.whitespace_width = 0
		self.line = line
		self.tab_width = config.tab_size
		self.tabs = config.tabs
		self.trim_trailing_whitespace = config.trim_trailing_whitespace
		# Split the line to initial whitespace,
		# non-whitespace, optional '\\' OR trailing whitespace, and EOL:
		m = re.fullmatch(rb'([\t ]*)(.*?)((?<!\\)[\t ]*|\\?)(\r?\n?)', line)
		# Note that trailing whitespace following a backslash is not considered whitespace which can be trimmed
		if not m:
			self.whitespaces = b''
			self.non_ws_line = line
			self.tail = b''
			self.eol = b''
			self.num_tabs = 0
			self.num_spaces = 0
			self.indent = 0
			return

		self.whitespaces, self.non_ws_line, self.tail, self.eol = m.groups(default=b'')

		if not self.non_ws_line:
			self.tail = self.whitespaces + self.tail
			self.whitespaces = b''

		# process spaces and tabs in whitespaces:
		# first tabs, then spaces are counted. Line with mixed spaces is ignored for indent analysis
		m = re.match(b'(\t*)( *)(\t)?', self.whitespaces)

		if m and not m[3]:
			self.num_tabs = len(m[1])
			self.num_spaces = len(m[2])
		else:
			# else Mixed tabs, ignore
			self.num_tabs = 0
			self.num_spaces = 0

		for c in self.whitespaces:
			if c == SPACE:
				self.whitespace_width += 1
			elif c == TAB:
				self.whitespace_width = self.whitespace_width + self.tab_width - self.whitespace_width % self.tab_width
		self.indent = LINE_INDENT_KEEP_CURRENT
		return

	def make_line(self, line_indent=LINE_INDENT_KEEP_CURRENT_NO_RETAB):

		if line_indent == LINE_INDENT_KEEP_CURRENT:
			line_indent = self.whitespace_width

		if line_indent == LINE_INDENT_KEEP_CURRENT_NO_RETAB:
			whitespaces = self.whitespaces
		elif self.tabs:
			whitespaces = b'\t' * (line_indent // self.tab_width) + b' ' * (line_indent % self.tab_width)
		else:
			whitespaces = b' ' * line_indent

		line = whitespaces + self.non_ws_line

		if not self.trim_trailing_whitespace or self.tail.endswith(b'\\'):
			line += self.tail

		line += self.eol
		return line

class parse_partial_lines:
	def __init__(self, config):
		self.config = config
		self.tab_size = config.tab_size
		self.lines = []
		self.line_num = 1

		return

	def read(self, lines_iter):
		self.contains_stray_cr = None
		self.lines.clear()
		line_num = self.line_num

		while (line := next(lines_iter, None)) is not None:
			p = parse_line(line, self.config)
			p.line_num = line_num
			if p.non_ws_line:
				if CR in p.non_ws_line:
					self.contains_stray_cr = line_num

			self.lines.append(p)
			if not p.tail.endswith(b'\\'):
				break

			line_num += 1
			continue

		return self.lines

	def __iter__(self):
		this_line = None

		for line in self.lines:
			this_line = line
			for c in line.non_ws_line:

				yield c, this_line
				this_line = None
				continue
			continue

		yield EOL, this_line
		return

def read_partial_lines(fd, config)->Generator[parse_partial_lines]:
	line_num = 1

	lines_iter = read_and_fix_lines(fd, config)
	partial_lines = parse_partial_lines(config)

	while (lines := partial_lines.read(lines_iter)):

		line_num += len(lines)
		yield partial_lines
		partial_lines.line_num = line_num
		continue

	return

def write_partial_lines(lines):
	# compose each of self.lines_to_write
	for line in lines:
		yield line.make_line(line.indent)
		continue
	return

class c_parser_state:
	def __init__(self, config):
		self.indent_size = config.indent

		self.open_braces = 0
		self.open_parens = 0
		self.next_level_adjustment = 0
		self.nesting_adjustment = 0
		self.indent_pos = 0		# previously calculated from meaningful line contents
		self.prev_last_non_ws = None
		self.last_non_ws = None
		self.prev_case_line = False
		self.block_stack = []
		self.statement_open = False
		return

	def init_new_line(self):
		self.initial_indent_level = self.open_braces + self.open_parens + self.nesting_adjustment
		self.initial_open_braces = self.open_braces	# At the start of line
		self.initial_open_parens = self.open_parens	# At the start of line
		self.prev_statement_open = self.statement_open
		self.prev_last_non_ws = self.last_non_ws
		self.opening_braces = 0
		self.closing_braces = 0
		return

	# Save and restore functions for saving the parser state
	# when a preprocessor conditional block begins.
	def save_state(self, preprocessor_line,
				prev_ignore_nesting_change=None, prev_restore_c_state=None):
		# Save a copy of C parser state

		if re.match(b'#else', preprocessor_line):
			if prev_restore_c_state == 'all':
				restore_c_state = prev_restore_c_state
				ignore_nesting_change = prev_ignore_nesting_change
			else:
				restore_c_state = not prev_restore_c_state
				ignore_nesting_change = not prev_ignore_nesting_change
		elif re.match(rb'#if(?:def\s+__cplusplus'
				rb'|\s+defined(?:\s*\(\s*__cplusplus\s*\)|\s+__cplusplus))',
						preprocessor_line):
			ignore_nesting_change = True
			restore_c_state = True
		elif re.match(rb'#(?:el)?if\s(?:0|\(0\)|FALSE)', preprocessor_line):
			restore_c_state = True
			ignore_nesting_change = True
		elif re.match(rb'#(?:el)?if\s(?:1|\(1\)|TRUE)', preprocessor_line):
			restore_c_state = False
			ignore_nesting_change = True
		else:
			ignore_nesting_change = False
			restore_c_state = 'all'

		return SimpleNamespace(
			ignore_nesting_change = ignore_nesting_change,
			restore_c_state = restore_c_state,
			open_braces = self.open_braces,
			open_parens = self.open_parens,
			statement_open = self.statement_open,
			prev_statement_open = self.prev_statement_open,
			ternary_open = self.ternary_open,
			next_level_adjustment = self.next_level_adjustment,
			nesting_adjustment = self.nesting_adjustment,
			last_non_ws = self.last_non_ws,
			prev_last_non_ws = self.prev_last_non_ws,
			prev_case_line = self.prev_case_line,
			block_stack = self.block_stack.copy(),
		)

	def restore_state(self, save):
		if not save.restore_c_state:
			return
		self.open_braces = save.open_braces
		self.open_parens = save.open_parens
		self.statement_open = save.statement_open
		self.prev_statement_open = save.prev_statement_open
		self.ternary_open = save.ternary_open
		self.next_level_adjustment = save.next_level_adjustment
		self.nesting_adjustment = save.nesting_adjustment
		self.last_non_ws = save.last_non_ws
		self.prev_last_non_ws = save.prev_last_non_ws
		self.prev_case_line = save.prev_case_line
		self.block_stack = save.block_stack
		return

	def close_statement(self):
		self.statement_open = False
		self.open_parens = 0
		self.ternary_open = 0
		return

	def push_block(self, open_braces=None):
		if open_braces is None:
			open_braces = self.open_braces
		self.block_stack.append(SimpleNamespace(
			nesting_adjustment=self.nesting_adjustment,
			open_braces=open_braces,
			))
		return

	def pop_block(self):
		adjustment = 0
		while self.block_stack and self.open_braces < self.block_stack[-1].open_braces:
			prev_adjustment = self.block_stack.pop(-1).nesting_adjustment
			if prev_adjustment < self.nesting_adjustment:
				adjustment += prev_adjustment - self.nesting_adjustment
			self.nesting_adjustment = prev_adjustment
		return adjustment

	def get_line_indent(self, pp_state):

		current_level = self.initial_indent_level + self.pop_block()

		# The indent adjustment is expected when:
		# a) or the previous line had positive braces change
		# or b) the previous line was not closed by ';'
		# The indent decrement is expected when:
		# a) the line starts with '}'
		# or b) the previous line was indented because the line before it was not closed with ';'

		# The nesting is adjusted back by number of closing braces in the start of line
		case_line = False

		level_adjustment = self.next_level_adjustment
		if self.open_parens == 0:
			self.next_level_adjustment = 0

		if re.match(rb'case\W|default\s*:', pp_state.non_ws_line):
			case_line = True
			level_adjustment = -1

			if pp_state.non_ws_line.endswith(b'{'):
				self.push_block()
				self.nesting_adjustment -= 1
		elif (m_closing := re.match(rb'(}\s*)+', pp_state.non_ws_line)) is not None:
			# Number of closing braces in the first sequence
			if self.open_braces <= 0:
				# Number of open braces must never go under zero
				level_adjustment = -current_level
			else:
				level_adjustment = -len(re.findall(b'}', m_closing[0]))
		elif self.opening_braces:
			if pp_state.non_ws_line[0] != BRACE_OPEN or self.closing_braces:
				level_adjustment = 0
			elif self.prev_case_line:
				self.push_block()
				self.nesting_adjustment -= 1
				level_adjustment = -1
			elif self.initial_open_braces == 0:
				return 0
			else:
				level_adjustment = 0
		elif self.last_non_ws == BRACE_OPEN or self.last_non_ws == BRACE_CLOSE:
			pass
		elif not self.prev_statement_open:
			# match word:, but not word::
			# A label is only recognized if the last statement is closed
			if re.match(rb'\w+\s*:(?!:)', pp_state.non_ws_line):
				# This is a label line, make the indent zero
				return 0
			elif self.initial_open_parens == 0 and self.last_non_ws == PAREN_CLOSE:
				# This statement hasn't been closed. Add one indent to the following lines
				self.next_level_adjustment = 1
			elif self.statement_open:
				self.next_level_adjustment = 1
			elif not pp_state.non_ws_line:
				self.next_level_adjustment = level_adjustment
		elif not self.statement_open:
			# Previous statement is still open, but it closes at this line
			# All parentheses closed here
			if self.prev_last_non_ws == COMMA:
				# Previous line ended with a comma
				# No additional indent in the current line
				level_adjustment = 0
		elif self.prev_last_non_ws == COMMA \
			or self.last_non_ws == COMMA:
			# All parentheses closed here
			# Previous line ended with a comma or this line ends with a comma
			# No additional indent in the current line
			level_adjustment = 0
			self.last_non_ws = COMMA
		elif self.initial_open_parens == 0 \
			and (self.prev_last_non_ws == PAREN_CLOSE \
			or self.last_non_ws == PAREN_CLOSE):
			# No open parentheses at the start of this line
			level_adjustment = 0
			# This statement hasn't been closed. Add one indent to the following lines
			self.next_level_adjustment = 1
		elif not pp_state.non_ws_line:
			self.next_level_adjustment = level_adjustment

		if self.open_braces < 0:
			# Number of open braces must never go under zero
			self.open_braces = 0
			self.open_parens = 0
		self.prev_case_line = case_line
		if self.last_non_ws == BRACE_OPEN \
				or self.last_non_ws == BRACE_CLOSE:
			# Force number of open parentheses to zero if a statement is closed
			# Note that we don't check for a semicolon since it can be present inside parentheses
			self.open_parens = 0

		current_level += level_adjustment
		if current_level <= 0:
			return 0
		line_indent = self.indent_size * current_level
		# Will not limit the in-parentheses indent change to extending only
		#if self.initial_open_parens and line_indent < pp_state.whitespace_width:
		#	return LINE_INDENT_KEEP_CURRENT
		return line_indent

class pre_parsing_state:
	def __init__(self, config, log_handler):
		self.log_handler = log_handler
		# Set to True when a line is joined to the next with a /* */ comment which crosses EOL
		self.comment_open = False
		self.comment_indent_ws:bytes = None	# Whitespaces in the first line of a multiline comment
		self.comment_indent_adjustment = 0	# Change of whitespace width in the first line of a multiline comment
		self.ends_with_open_comment = False
		self.starts_with_open_comment = False
		self.slash_slash_comment = False
		self.preprocessor_line = None
		self.if_stack = []
		self.non_ws_line = bytearray()
		self.non_ws_line_started = False
		# A preprocessor line can span multiple lines by joining them with a comment which spans lines
		# If a preprocessor line is running, we only parse comments, character literals and strings
		self.first_preprocessor_line = False
		return

	def init_new_line(self, first_parse_line:parse_line, c_state:c_parser_state):
		self.slash_slash_comment = False
		self.empty = True
		self.line_num = first_parse_line.line_num
		self.non_ws_line.clear()
		self.whitespace_width = first_parse_line.whitespace_width
		self.whitespaces = first_parse_line.whitespaces

		if not self.comment_open:
			# A preprocessor line can span multiple lines by joining them with a comment which spans lines
			# If a preprocessor line is running, we only parse comments, character literals and strings
			self.preprocessor_line = None
			# non_ws_line_started means there's non-whitespace contents
			# in a line possibly joined by an open /* */ comment
			# non_ws_line_started is set to False for a line which doesn't begin with an open comment
			self.non_ws_line_started = False
			self.starts_with_open_comment = False
		else:
			self.starts_with_open_comment = True

		if self.preprocessor_line is None:
			c_state.init_new_line()
		elif self.first_preprocessor_line is True:
			self.first_preprocessor_line = False
			self.preprocessor_line = b'#'
		return

	def get_line_indent(self, c_state:c_parser_state):
		if self.starts_with_open_comment:
			if c_state.initial_open_braces == 0:
				return LINE_INDENT_KEEP_CURRENT
			if self.whitespace_width == 0:
				return LINE_INDENT_KEEP_CURRENT
			if self.comment_indent_ws is None \
				or not self.whitespaces.startswith(self.comment_indent_ws):
				return LINE_INDENT_KEEP_CURRENT
			if self.whitespace_width > self.comment_indent_adjustment:
				return self.whitespace_width - self.comment_indent_adjustment
			return 0

		if self.non_ws_line:
			if self.ends_with_open_comment:
				self.comment_indent_ws = self.whitespaces
		elif self.non_ws_line_started:
			# There's been non-comment tokens in the (joined by comments) line
			if not self.ends_with_open_comment:
				self.comment_indent_ws = None
			#Use c_state calculation
		# No meaningful data in this line so far, besides from comments.
		# If there are leading whitespaces, they are reformatted to the expected indent
		elif self.whitespace_width == 0:
			# Text (perhaps comment) starts from start of line. Keep it that way
			return 0
		elif c_state.initial_open_braces == 0:
			# Oneline and '//' comments at the top level are not reindented
			return LINE_INDENT_KEEP_CURRENT

		return c_state.get_line_indent(self)

	def parse_c_line(self, partial_lines:parse_partial_lines, c_state:c_parser_state):

		last_whitespace = False

		ii = iter(partial_lines)
		next_c = next(ii, None)
		self.init_new_line(partial_lines.lines[0], c_state)
		non_ws_line = self.non_ws_line

		while next_c is not None:

			c, line = next_c

			if c is EOL:
				break

			if line is not None and line.whitespace_width:
				last_whitespace = True

			next_c = next(ii, None)

			if c == SPACE or c == TAB:
				last_whitespace = True
				continue

			self.empty = False

			if self.comment_open:
				# Look for the next */
				if c == ASTERISK and next_c[0] == SLASH:
					next_c = next(ii, None)
					self.comment_open = False
					last_whitespace = True
				continue

			if c == SLASH:
				if next_c[0] == SLASH:
					self.slash_slash_comment = True
					break
				if next_c[0] == ASTERISK:
					self.comment_open = True
					next_c = next(ii, None)
					continue

			if last_whitespace and non_ws_line:
				# Replace /**/ comments and multiple spaces with a single space
				non_ws_line.append(SPACE)
			last_whitespace = False

			if not self.non_ws_line_started:
				# Non white-space contents begins
				self.non_ws_line_started = True
				# Non white-space contents begins
				if c == POUND:
					self.preprocessor_line = b'#'
					self.first_preprocessor_line = None
					continue
				if c == BRACE_OPEN:
					c_state.prev_statement_open = False
				elif c == BRACE_CLOSE:
					c_state.prev_statement_open = False

			non_ws_line.append(c)

			if c == WIDE_STRING_PREFIX and \
				(next_c[0] == DOUBLE_QUOTE or next_c[0] == SINGLE_QUOTE):
				c, line = next_c
				non_ws_line.append(c)
				next_c = next(ii, None)

			if c == DOUBLE_QUOTE or c == SINGLE_QUOTE:
				cc = c
				while next_c[0] is not EOL:
					c, line = next_c
					if line is not None:
						line.indent = LINE_INDENT_KEEP_CURRENT_NO_RETAB
					next_c = next(ii, None)
					if cc == c:
						# The string contents doesn't need to go to the aggregate line, only quotes
						non_ws_line.append(c)
						break
					if c == BACKSLASH:
						next_c = next(ii, None)
					continue
				continue

			if self.preprocessor_line is not None:
				continue

			c_state.last_non_ws = c
			# Only care about nesting in non-preprocessor line
			if c == BRACE_OPEN:
				c_state.opening_braces += 1
				c_state.close_statement()
			elif c == BRACE_CLOSE:
				c_state.closing_braces += 1
				c_state.open_parens = 0
				c_state.statement_open = False
			elif c == PAREN_OPEN:
				c_state.open_parens += 1
				c_state.statement_open = True
			elif c == PAREN_CLOSE:
				c_state.open_parens -= 1
			elif c == SEMICOLON:
				c_state.statement_open = False
				if c_state.open_parens == 0:
					c_state.ternary_open = 0
			elif c == QUESTION:
				c_state.ternary_open += 1
			elif c == COLON:
				if next_c[0] == COLON:
					non_ws_line.append(COLON)
					next_c = next(ii, None)
				elif c_state.ternary_open:
					c_state.ternary_open -= 1
				else:
					# A single ':' means it'a a label
					c_state.statement_open = False
			elif is_alphanumeric(c):
				while 1:
					c, line = next_c
						# Next line whitespaces must be zero
					if not is_alphanumeric(c):
						break
					c_state.last_non_ws = c
					non_ws_line.append(c)
					next_c = next(ii, None)
					continue
				c_state.statement_open = True
				continue
			else:
				c_state.statement_open = True
			continue

		if non_ws_line and (non_ws_line[0] == BRACE_OPEN \
			or non_ws_line[0] == BRACE_CLOSE):
			# Force number of open parentheses to zero if a statement is closed
			# Note that we don't check for a semicolon since it can't be present inside parentheses
			c_state.initial_open_parens = 0

		c_state.open_braces += c_state.opening_braces - c_state.closing_braces
		return

	def finalize_lines(self, lines_to_write, c_state:c_parser_state):
		self.ends_with_open_comment = self.comment_open
		if self.preprocessor_line is not None:
			if self.first_preprocessor_line is None:
				self.preprocessor_line += self.non_ws_line
				self.first_preprocessor_line = True
			elif self.first_preprocessor_line is False:
				# self.first_preprocessor_line can be None, True, False
				return

			# not changing indents in preprocessor lines. Also, they are skipped for the purpose of indent detection
			# first_preprocessor_line is set to true if this line has first non-whitespace after '#' character
			# Get the keyword
			m = re.match(rb'#\w+', self.preprocessor_line, re.ASCII)
			if m is None:
				return

			if m[0] == b'#if' or m[0] == b'#ifdef' or m[0] == b'#ifndef':
				# Save parsing state
				ps = c_state.save_state(self.preprocessor_line)
				self.if_stack.append(ps)
				return

			if not self.if_stack:
				return
			if m[0] == b'#elif' or m[0] == b'#else':
				prev_ps = self.if_stack.pop(-1)
				# Save new parsing state
				ps = c_state.save_state(self.preprocessor_line,
							prev_ps.ignore_nesting_change, prev_ps.restore_c_state)
				self.if_stack.append(ps)
				c_state.restore_state(prev_ps)
				return

			if m[0] == b'#endif':
				prev_ps = self.if_stack.pop(-1)
				if (prev_ps.restore_c_state and prev_ps.open_parens != c_state.open_parens) \
					or (not prev_ps.ignore_nesting_change and prev_ps.open_braces != c_state.open_braces):
					self.log_handler('A preprocessor conditional construct in line %d makes mismatched nesting level' % self.line_num)
				c_state.restore_state(prev_ps)
			return

		if self.empty:
			# Nothing to indent
			return

		line_indent = self.get_line_indent(c_state)

		lines_to_write[0].indent = line_indent

		if line_indent > 0 and \
				not self.starts_with_open_comment \
				and self.ends_with_open_comment:
			self.comment_indent_ws = self.whitespaces
			self.comment_indent_adjustment = self.whitespace_width - line_indent

		return

def format_c_file(fd_in, config, error_handler=format_err_handler):
	preproc_if_nesting = []

	for lines_to_write, pp_state, c_state in parse_c_file(fd_in, config, error_handler):

		c_state:c_parser_state
		pp_state:pre_parsing_state

		pp_state.finalize_lines(lines_to_write, c_state)

		yield from write_partial_lines(lines_to_write)
		continue

	return

def read_and_fix_lines(fd : io.BytesIO, config):
	fix_cr_eol = config.fix_eol

	if not fix_cr_eol:
		for line in fd:
			yield line
		return

	cr_pattern = re.compile(b'\r(?!\n)')
	prev_lf = False

	for line in fd:
		ends_crlf = line.endswith(b'\r\n')

		if ends_crlf and line.find(b'\r', 0, len(line) - 2) == -1:
			yield line
		elif not ends_crlf and line.find(b'\r') == -1:
			yield line
		else:
			if line.endswith(b'\r'):
				# Last line in the file ends with a single CR
				line += b'\n'
			# Split by standalone CR
			splitlines = cr_pattern.split(line)
			# If line had a '\r' in the first character, and previous line had a single '\n' in the end,
			# treat is a s single '\n\r' line separator
			if prev_lf and len(splitlines[0]) == 0:
				splitlines.pop(0)
			# Append '\n' to lines split by '\r'
			for i in range(len(splitlines)-1):
				splitlines[i] += b'\n'
			yield from splitlines

		prev_lf = not ends_crlf
		continue
	return

# A line in C file can be composed from several lines, concatenated with '\\' as the last character
# fix_cr_eol option treats standalone '\r' as line separators and replaces them with \n.
# We're using BytesIO object which doesn't support universal newlines (just as other stream in binary mode).
# Thus, we have to handle standalone '\r' ourselves.
def parse_c_file(fd : io.BytesIO,
				config=SimpleNamespace(tab_size=4, tabs=True,
							trim_trailing_whitespace=True,
							fix_eol=False,
							),
				log_handler=format_err_handler,
				)->Generator[(parse_partial_lines, pre_parsing_state, c_parser_state)]:
	# We gather the combined line, cleaned of comments and other whitespaces.
	# parse_partial_lines returns the full set of partial lines
	# We only analyze leading whitespaces in the first partial line

	pp_state = pre_parsing_state(config, log_handler)
	c_state = c_parser_state(config)

	for partial_lines in read_partial_lines(fd, config):

		if partial_lines.contains_stray_cr is not None:
			log_handler('Line %d contains a stray CR character' % partial_lines.contains_stray_cr)

		pp_state.parse_c_line(partial_lines, c_state)

		yield partial_lines.lines, pp_state, c_state
		continue

	return

def fix_file_lines(in_fd, config):

	for line in read_and_fix_lines(in_fd, config):
		p = parse_line(line, config)
		line_indent = LINE_INDENT_KEEP_CURRENT_NO_RETAB
		yield p.make_line(line_indent)
	return

def format_data(data, format_spec, error_handler=None):
	if not format_spec.skip_indent_format:
		yield from format_c_file(io.BytesIO(data), format_spec, error_handler)
	elif format_spec.trim_trailing_whitespace or format_spec.fix_eol:
		yield from fix_file_lines(io.BytesIO(data), format_spec)
	else:
		yield data

def get_style_str(style):
	if not style:
		return 'None'
	result = []

	if style.tabs:
		if style.tab_width:
			result = ['tabs' + str(style.tab_width)]
		else:
			result = ['tabs']

	if style.spaces:
		result.append('spaces' + str(style.indent))

	if not result:
		return 'None'

	return '+'.join(result)

def get_file_list(glob_list, input_directory, output_path):
	file_list = []
	output_filename = None
	if output_path == '-':
		# Output goes to stdout
		output_directory = None
	elif not output_path:
		# Output path is not specified. Formatting is written back to same file
		output_directory = Path()
	else:
		output_path = Path(output_path)
		if output_path.is_dir():
			output_directory = output_path
		else:
			# Output path is a filename.
			output_directory = Path()
			output_filename = output_path

	import glob
	for spec in glob_list:
		input_spec = Path(input_directory, spec)
		for filename in glob.iglob(str(input_spec), recursive=True):
			# Split the directory prefix
			filename = Path(filename)
			if not filename.is_file():
				continue
			if output_filename is not None:
				out_filename = output_filename
				# The rest goes to stdout
				output_filename = None
				output_directory = None
			elif output_directory is None:
				# Send to stdout
				out_filename = None
			elif not input_directory:
				out_filename = output_directory.joinpath(filename)
			elif filename.is_relative_to(input_directory):
				out_filename = output_directory.joinpath(filename.relative_to(input_directory))
			else:
				out_filename = output_directory.joinpath(filename.name)
			file_list.append(SimpleNamespace(input_filename=filename, output_filename=out_filename))
			continue

	return file_list

def main():
	import argparse
	parser = argparse.ArgumentParser(description="Reformat a file or detect indentation in the given files", allow_abbrev=True)
	parser.add_argument("infile", type=Path, help="Filenames or glob specifications (quoted), to process", nargs='*')
	parser.add_argument("--out", '-O', dest='out_file', help="Result printout destination; default to stdout",
					nargs='?')
	parser.add_argument("--current-dir", '-C', help="Base directory for glob specifications or file list processing", default='')
	parser.add_argument("--quiet", '-q', action='store_true', help="Don't print progress messages")
	parser.add_argument("--style", '-s', help="Indentation style: 'spaces', 'tabs', or 'keep', which will prevent reindentation.",
					choices=['spaces', 'tabs', 'keep'], default='tabs')
	parser.add_argument("--tab-size", help="Tab size, from 1 to 16, default 4.",
					choices=range(1,17), type=int, default='4', metavar="1...16")
	parser.add_argument("--indent-size", help="Tab size, from 1 to 16, default 4.",
					choices=range(1,17), type=int, default='4', metavar="1...16")
	parser.add_argument("--trim-whitespace", default=False, action='store_true',
					help="Trim trailing whitespaces.")
	parser.add_argument("--fix-eols", default=False, action='store_true',
					help="Fix lonely carriage returns into line feed characters. Git by default doesn't do that.")

	options = parser.parse_args()

	file_list = get_file_list(options.infile, options.current_dir, options.out_file)

	if not file_list:
		return parser.print_usage(sys.stderr)

	conf = SimpleNamespace(
		tab_size = options.tab_size,
		skip_indent_format = options.style == 'keep',
		indent = options.indent_size,
		trim_trailing_whitespace = options.trim_whitespace,
		fix_eol = options.fix_eols,
		tabs = options.style == 'tabs')

	for file in file_list:
		if (conf.skip_indent_format \
			and not conf.trim_trailing_whitespace and not conf.fix_eol):
				continue

		# Read data _before_ opening the output file, to allow processing in place
		data = Path.read_bytes(file.input_filename)

		if not file.output_filename:
			# open() can take a duplicated file descriptor
			file.output_filename = os.dup(sys.stdout.fileno())
			options.quiet = True
			def error_handler(s):
				print(s,file=sys.stderr)
				return
		else:
			def error_handler(s):
				print("File %s: %s" % (file.input_filename, s),file=sys.stderr)
				return

		with open(file.output_filename, 'wb') as out_fd:
			if not options.quiet:
				print("Formatting: %s" % file.input_filename, file=sys.stderr)
			for data in format_data(data, conf, error_handler):
				out_fd.write(data)

		continue

	return 0

import hashlib
# SHA1 of this file is used to invalidate SHA1 map file if this file changes
sha1 = hashlib.sha1(Path(__file__).read_bytes(),usedforsecurity=False).digest()

if sys.version_info < (3, 8):
	sys.exit("indentation: This package requires Python 3.8+")

if __name__ == "__main__":
	try:
		sys.exit(main())
	except FileNotFoundError as fnf:
		print("ERROR: %s: %s" % (fnf.strerror, fnf.filename), file=sys.stderr)
		sys.exit(1)
	except KeyboardInterrupt:
		# silent abort
		sys.exit(130)
