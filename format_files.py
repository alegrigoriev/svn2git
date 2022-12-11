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
BRACKET_OPEN=ord(b'[')
BRACKET_CLOSE=ord(b']')
PAREN_OPEN=ord(b'(')
PAREN_CLOSE=ord(b')')
SINGLE_QUOTE=ord(b"'")
DOUBLE_QUOTE=ord(b'"')
WIDE_STRING_PREFIX=ord(b'L')
QUOTED_LITERAL=b"''"
STRING_LITERAL=b'""'
BACKSLASH=ord(b'\\')
BACKSLASH_SEPARATOR=0xFEED
SEMICOLON=ord(b';')
# A colon may appear in a declaration as field size specifier,
# in a constructor declaration, after a label or case statement,
# and in a ternary expression
COLON=ord(b':')
QUESTION=ord(b'?')
COMMA=ord(b',')
COLONCOLON=b'::'
EQUAL=ord(b'=')
EQUAL_EQUAL=b'=='
BANG=ord(b'!')
BANG_EQUAL=b'!='
PLUS=ord(b'+')
PLUS_PLUS=b'++'
PLUS_EQUAL=b'+='
MINUS=ord(b'-')
MINUS_MINUS=b'--'
MINUS_EQUAL=b'-='
SLASH_EQUAL=b'/='
PERCENT=ord(b'%')
PERCENT_EQUAL=b'%='
ASTERISK_EQUAL=b'*='
OR=ord(b'|')
OR_LOGICAL=b'||'
OR_EQUAL=b'|='
AND=ord(b'&')
AND_EQUAL=b'&='
AND_LOGICAL=b'&&'
XOR=ord(b'^')
XOR_EQUAL=b'^='
LESS=ord(b'<')
LESS_EQUAL=b'<='
LEFT_SHIFT=b'<<'
LEFT_SHIFT_EQUAL=b'<<='
GREATER=ord(b'>')
GREATER_EQUAL=b'<='
RIGHT_SHIFT=b'>>'
RIGHT_SHIFT_EQUAL=b'>>='
NOT=ord(b'~')
DOT=ord(b'.')
ARROW=b'->'

OPERATOR = b'operator'
OP = b'op'
ASSIGNMENT_OP = b'ASSIGN_OP'

FOR_TOKEN=b"for"
IF_TOKEN=b"if"
ELSE_TOKEN=b"else"
PENDING_ELSE_TOKEN=b"pending_else"
DO_TOKEN=b"do"
WHILE_TOKEN=b"while"
TRY_TOKEN=b"try"
CATCH_TOKEN=b"catch"
DO_WHILE_TOKEN=b"do_while"
PENDING_WHILE_TOKEN=b"pending_while"
SWITCH_TOKEN=b"switch"
NAMESPACE_TOKEN=b"namespace"
CASE_TOKEN=b"case"
DEFAULT_TOKEN=b"default"
RETURN_TOKEN=b"return"
PRIVATE_TOKEN=b"private"
TEMPLATE_TOKEN=b"template"

ALPHANUM_TOKEN="alphanumeric"
PREPROCESSOR_LINE=b'#'
IF_LINE=b'#if'
ELSE_LINE=b'#else'
ELIF_LINE=b'#elif'
IFDEF_LINE=b'#ifdef'
IFNDEF_LINE=b'#ifndef'
ENDIF_LINE=b'#endif'
DEFINE_LINE=b'#define'

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
				# Last CR in the file
				if p.eol == b'\r':
					self.contains_stray_cr = line_num
				break

			line_num += 1
			continue

		return self.lines

	def __iter__(self):
		this_line = None

		for line in self.lines:
			this_line = line
			character_pos = 0
			for c in line.non_ws_line:

				yield c, this_line, character_pos
				this_line = None
				if c != TAB:
					character_pos += 1
				else:
					character_pos += self.tab_size
					character_pos -= character_pos % self.tab_size
				continue

			if this_line is not None and line.tail.endswith(b'\\'):
				# This is an empty line, make sure to pass this_line
				yield BACKSLASH_SEPARATOR, this_line, None
				this_line = None
			continue

		yield EOL, this_line, None
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

alphanum_tokens = {
	IF_TOKEN : IF_TOKEN,
	FOR_TOKEN : FOR_TOKEN,
	ELSE_TOKEN : ELSE_TOKEN,
	WHILE_TOKEN : WHILE_TOKEN,
	DO_TOKEN : DO_TOKEN,
	SWITCH_TOKEN : SWITCH_TOKEN,
	CASE_TOKEN :  CASE_TOKEN,
	DEFAULT_TOKEN : DEFAULT_TOKEN,
	RETURN_TOKEN : RETURN_TOKEN,
	b'throw' : RETURN_TOKEN,
	TRY_TOKEN : TRY_TOKEN,
	CATCH_TOKEN : CATCH_TOKEN,
	OPERATOR : OPERATOR,
	PRIVATE_TOKEN : PRIVATE_TOKEN,
	b'public' : PRIVATE_TOKEN,
	b'protected' : PRIVATE_TOKEN,
	NAMESPACE_TOKEN : NAMESPACE_TOKEN,
	TEMPLATE_TOKEN : TEMPLATE_TOKEN,
}

def decode_alphanumeric_token(s:bytes):
	# Note that we don't return 's' itself,
	# but a global constant,
	# to be able to match it by 'is' operator,
	token = alphanum_tokens.get(s, None)
	if token is not None:
		return token

	return (ALPHANUM_TOKEN, s)

preprocessor_tokens = {
	PREPROCESSOR_LINE : PREPROCESSOR_LINE,
	DEFINE_LINE : DEFINE_LINE,
	b'#undef' : DEFINE_LINE,
	IF_LINE : IF_LINE,
	ENDIF_LINE : ENDIF_LINE,
	IFDEF_LINE : IFDEF_LINE,
	IFNDEF_LINE : IFNDEF_LINE,
	ELSE_LINE : ELSE_LINE,
	ELIF_LINE : ELIF_LINE,
}

def decode_preprocessor_token(s:bytes):
	# Note that we don't return 's' itself,
	# but a global constant,
	return preprocessor_tokens.get(s, s)

class c_parser_state:
	def __init__(self, config):
		self.indent_size = config.indent
		self.indent_case = config.indent_case
		self.tab_size = config.tab_size
		self.use_tabs = config.tabs
		self.reindent_continuation = config.reindent_continuation.any
		self.reindent_continuation_smart = config.reindent_continuation.smart
		self.reindent_continuation_extend = config.reindent_continuation.extend
		self.max_to_parenthesis = config.reindent_continuation.max_to_parenthesis

		# The context token specifies a special parsing state: IF, FOR, SWITCH, CASE
		self.context = None
		self.nesting_level = 0
		self.open_braces = 0
		self.open_parens = 0
		self.statement_continuation = False
		self.this_line_indent_pos = 0
		self.assignment_open = False	# An assignment operator is present on the upper level of parentheses
		self.expression_open = False	# A non-assignment operator is present on the upper level of parentheses
		self.ternary_open = 0
		self.composite_statement_token = None
		self.composite_statement_stack = []
		self.block_stack = []
		self.statement_open = None
		self.whitespace_adjustment = 0
		self.line_width_for_adjustment = self.max_to_parenthesis*2
		self.expression_stack = []
		return

	def init_new_line(self, first_line):
		self.whitespace_width = first_line.whitespace_width
		self.first_line_width = len(first_line.non_ws_line)

		self.this_line_indent_pos = None	# Is set at the first token
		self.initial_open_braces = self.open_braces	# At the start of line
		self.prev_token = None
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
			context = self.context,
			nesting_level = self.nesting_level,
			open_braces = self.open_braces,
			open_parens = self.open_parens,
			statement_open = self.statement_open,
			statement_continuation = self.statement_continuation,
			assignment_open = self.assignment_open,
			expression_open = self.expression_open,
			ternary_open = self.ternary_open,
			expression_stack = self.expression_stack.copy(),
			composite_statement_stack = self.composite_statement_stack.copy(),
			composite_statement_token = self.composite_statement_token,
			whitespace_adjustment = self.whitespace_adjustment,
			line_width_for_adjustment = self.line_width_for_adjustment,
			block_stack = self.block_stack.copy(),
		)

	def restore_state(self, save):
		if not save.restore_c_state:
			return
		self.context = save.context
		self.nesting_level = save.nesting_level
		self.open_braces = save.open_braces
		self.open_parens = save.open_parens
		self.statement_open = save.statement_open
		self.statement_continuation = save.statement_continuation
		self.expression_open = save.expression_open
		self.assignment_open = save.assignment_open
		self.ternary_open = save.ternary_open
		self.expression_stack = save.expression_stack
		self.composite_statement_token = save.composite_statement_token
		self.composite_statement_stack = save.composite_statement_stack
		self.whitespace_adjustment = save.whitespace_adjustment
		self.line_width_for_adjustment = save.line_width_for_adjustment
		self.block_stack = save.block_stack
		return

	# A simple statement gets open (started) when a non-label token appears,
	# other than a composite statement token (if, for, while, do, switch, etc)
	# A label token is: alphanumeric identifier with a colon ':',
	# or 'case <expression>:'
	# A colon in an open statement is either a part of tertiary operator,
	# or part of structure or class header, or part of constructor header.
	def open_statement(self):
		if self.statement_open or self.context is not None:
			return
		self.statement_open = True
		self.assignment_open = False
		self.expression_open = False
		self.statement_continuation = False
		self.composite_statement_token = None
		self.set_line_indent(0)
		return

	def close_statement(self):
		self.statement_open = None
		self.assignment_open = False
		self.expression_open = False
		self.statement_continuation = False
		self.open_parens = 0
		self.ternary_open = 0
		self.context = None
		self.expression_stack = []
		self.whitespace_adjustment = 0
		self.line_width_for_adjustment = self.max_to_parenthesis*2
		self.pop_composite_statement()
		return

	def push_expression_stack(self,
							pop_composite_statement_token=None,
							pop_statement_open=False,
							use_token_position=None,
							assignment_open=False,
							expression_open=True,
							statement_continuation=False,
							parens_increment=1,
							indent_increment=None):

		if self.this_line_indent_pos is None:
			self.adjust_expression_state(open_assignment=assignment_open,
										open_expression=expression_open)
			self.set_line_indent()

		stack_item = SimpleNamespace()
		stack_item.assignment_open = assignment_open
		stack_item.expression_open = expression_open
		stack_item.pop_statement_open = pop_statement_open
		stack_item.pop_composite_statement_token = pop_composite_statement_token

		stack_item.parens_increment = parens_increment
		if indent_increment is not None:
			stack_item.indent_increment = indent_increment
		else:
			stack_item.indent_increment = parens_increment

		token_position = self.token_position + self.this_line_indent_pos
		next_token_position = self.next_token_position
		if next_token_position is not None:
			next_token_position += self.this_line_indent_pos

		if use_token_position:
			if next_token_position is not None:
				use_token_position = self.adjust_position_to_tab(next_token_position, 1)
			else:
				use_token_position = self.adjust_position_to_tab(token_position+1, 1)
			stack_item.use_token_position=use_token_position	# Token position relative to the non-whitespace line begin
		elif self.expression_stack:
			stack_item.use_token_position = None
			stack_top = self.expression_stack[-1]
			# Inherit this position from the previous expression level
			# to limit "extend_only" indents.
			next_token_position = (
				stack_top.use_token_position or
				stack_top.next_token_position or
				(stack_top.token_position and
				(stack_top.token_position + 1)))
		else:
			stack_item.use_token_position = None

		stack_item.token_position = token_position
		stack_item.next_token_position = next_token_position
		stack_item.this_line_indent_pos = self.this_line_indent_pos			# set to this line indent
		stack_item.absolute_indent_position = None	# absolute position for the next line continuation at this expression level
		stack_item.pop_open_parens = self.open_parens
		stack_item.pop_assignment_open = self.assignment_open
		stack_item.pop_expression_open = self.expression_open
		stack_item.pop_statement_continuation = self.statement_continuation
		stack_item.statement_continuation = statement_continuation
		stack_item.composite_statement_token = self.composite_statement_token

		stack_item.indent_adjustment = None
		stack_item.closing_token_position = None

		self.expression_stack.append(stack_item)

		self.assignment_open = assignment_open
		self.expression_open = expression_open
		self.statement_continuation = statement_continuation
		self.open_parens += parens_increment
		return stack_item

	def pop_expression_stack(self):
		if not self.expression_stack:
			return None

		stack_item = self.expression_stack.pop(-1)
		self.open_parens = stack_item.pop_open_parens
		self.assignment_open = stack_item.pop_assignment_open
		self.expression_open = stack_item.pop_expression_open
		self.statement_continuation = stack_item.pop_statement_continuation
		self.statement_open = stack_item.pop_statement_open
		self.composite_statement_token = stack_item.pop_composite_statement_token

		return stack_item

	def finalize_stack_item(self,
							stack_loc,
							indent_pos,
							indent_adjustment):

		if stack_loc.absolute_indent_position is not None:
			return stack_loc.absolute_indent_position

		if indent_pos is None:
			indent_pos = stack_loc.this_line_indent_pos

		if self.reindent_continuation_smart:
			token_position = stack_loc.use_token_position
		else:
			token_position = None

		indent_pos = self.adjust_position_to_tab(indent_pos)

		if token_position:
			if indent_adjustment is None:
				indent_adjustment = int(
					stack_loc.expression_open or
					stack_loc.assignment_open)
				if stack_loc.indent_adjustment is None:
					stack_loc.indent_adjustment = indent_adjustment
				indent_adjustment = indent_adjustment or stack_loc.indent_increment
			indent_pos += self.indent_size * indent_adjustment

			token_position = self.adjust_position_to_tab(token_position)
			# Adjust this token position
			new_line_length = token_position + self.first_line_width
			if new_line_length > self.line_width_for_adjustment:
				# With this indent the line would become too long
				token_position = indent_pos
				# Save the new adjustment
				self.whitespace_adjustment = token_position - self.whitespace_width
			elif token_position > self.max_to_parenthesis:
				# This indent would be too far
				token_position = indent_pos
				# Save the new adjustment
				self.whitespace_adjustment = token_position - self.whitespace_width

			if token_position >= indent_pos:
				indent_pos = token_position
				stack_loc.closing_token_position = token_position
				increment = 0
		else:
			if indent_adjustment is None:
				indent_adjustment = int(
					stack_loc.expression_open or
					stack_loc.assignment_open)
				if stack_loc.indent_adjustment is None:
					stack_loc.indent_adjustment = indent_adjustment
			stack_loc.token_position = None
			increment = stack_loc.indent_increment or indent_adjustment

			if increment != 0:
				# Only round down is non-zero increment
				indent_pos += increment * self.indent_size
				indent_pos -= indent_pos % self.indent_size
				if indent_pos < 0:
					indent_pos = 0
			if stack_loc.use_token_position is not None:
				if stack_loc.use_token_position == 0:
					stack_loc.closing_token_position = 0
				else:
					stack_loc.closing_token_position = indent_pos

		stack_loc.absolute_indent_position = indent_pos
		return indent_pos

	def adjust_position_to_tab(self, indent_pos, adjustment=0):
		if not self.use_tabs:
			return indent_pos
		indent_pos += adjustment
		return indent_pos - indent_pos % min(self.tab_size, self.indent_size)

	def adjust_expression_state(self,
					open_expression=None,
					open_assignment=None):

		if not self.expression_stack:
			if open_expression is not None:
				self.expression_open = open_expression
			if open_assignment is not None:
				self.assignment_open = open_assignment
			return

		stack_top = self.expression_stack[-1]

		if stack_top.absolute_indent_position is not None:
			return

		if open_expression is not None:
			stack_top.expression_open = open_expression
		if open_assignment is not None:
			stack_top.assignment_open = open_assignment
		return

	def set_popped_stack_loc_indent(self, stack_top):
		if self.this_line_indent_pos is not None and self.expression_stack:
			return

		if not self.reindent_continuation:
			if self.this_line_indent_pos is None:
				self.this_line_indent_pos = self.whitespace_width
			return self.this_line_indent_pos

		if stack_top.absolute_indent_position is None:
			indent_pos = self.this_line_indent_pos
			for stack_loc in self.expression_stack:
				indent_pos = self.finalize_stack_item(stack_loc, indent_pos,
											stack_loc.indent_adjustment)
				continue
			indent_pos = self.finalize_stack_item(stack_top, indent_pos, None)
		else:
			indent_pos = stack_top.absolute_indent_position

		if stack_top.closing_token_position is not None:
			indent_pos = stack_top.closing_token_position

		indent_pos = self.adjust_top_continuation_width(indent_pos, stack_top)
		if self.this_line_indent_pos is None:
			self.this_line_indent_pos = indent_pos
		return indent_pos

	def adjust_top_continuation_width(self, indent_pos, stack_top):
		if self.reindent_continuation_smart or not self.reindent_continuation_extend:
			return indent_pos

		adjusted_whitespace_width = self.whitespace_width + self.whitespace_adjustment

		token_position = stack_top.next_token_position
		if token_position is not None:
			token_position = self.adjust_position_to_tab(token_position, self.indent_size - 1)
			adjusted_whitespace_width = min(adjusted_whitespace_width, token_position)

		if indent_pos < adjusted_whitespace_width:
			return adjusted_whitespace_width
		return indent_pos

	# Set the indent_level (in indent units) for the first token.
	# Absolute indent will be set as is.
	# For a statement continuation, relative indent is ignored
	def set_line_indent(self, indent_adjustment=None,
						absolute=None):

		if self.prev_token is not None:
			if indent_adjustment is not None and self.expression_stack:
				self.expression_stack[-1].indent_adjustment = indent_adjustment
			return self.this_line_indent_pos

		if self.this_line_indent_pos is not None:
			return self.this_line_indent_pos

		indent_pos = self.whitespace_width

		if self.expression_stack:

			if not self.reindent_continuation:
				# Not re-indenting continuation lines
				self.this_line_indent_pos = indent_pos
				return indent_pos

			stack_top = self.expression_stack[-1]

			indent_pos = self.this_line_indent_pos
			for stack_loc in self.expression_stack:
				if stack_loc is stack_top:
					# indent_adjustment is only used for the topmost stack item
					if indent_adjustment is not None:
						stack_loc.indent_adjustment = indent_adjustment
				indent_pos = self.finalize_stack_item(stack_loc, indent_pos,
					stack_loc.indent_adjustment)
				continue
			if self.statement_continuation:
				indent_pos = self.adjust_top_continuation_width(indent_pos, stack_top)
		elif absolute is not None:
			indent_pos = absolute * self.indent_size
		else:
			indent_pos = self.indent_size * self.nesting_level
			keep_initial_indent = not self.open_braces and self.statement_open
			if indent_adjustment is not None:
				indent_pos += self.indent_size * indent_adjustment
				if indent_pos < 0:
					indent_pos = 0
				keep_initial_indent = False
			elif (self.expression_open or self.assignment_open or self.statement_continuation):
				indent_pos += self.indent_size

			if not self.statement_continuation:
				if keep_initial_indent:
					indent_pos = self.whitespace_width
				self.whitespace_adjustment = indent_pos - self.whitespace_width
				self.line_width_for_adjustment = max(self.first_line_width + indent_pos,
													self.max_to_parenthesis*2)
			elif not self.reindent_continuation:
				indent_pos = self.whitespace_width

		self.this_line_indent_pos = indent_pos
		return indent_pos

	def push_block(self, indent_adjustment=1):
		pop_indent = self.nesting_level - 1 + indent_adjustment

		self.block_stack.append(SimpleNamespace(
			composite_statement_stack=self.composite_statement_stack,
			nesting_level=self.nesting_level,
			open_braces=self.open_braces,
			pop_indent=pop_indent,
			composite_statement_token=self.composite_statement_token,
			))

		self.composite_statement_token = None
		self.composite_statement_stack = []

		self.set_line_indent(absolute=pop_indent)
		self.nesting_level += indent_adjustment
		self.open_braces += 1
		if not self.assignment_open:
			self.close_statement()
		return

	def pop_block(self, set_indent=True):
		if not self.block_stack:
			return False

		stack_loc = self.block_stack.pop(-1)
		self.composite_statement_stack = stack_loc.composite_statement_stack
		self.nesting_level = stack_loc.nesting_level
		self.open_braces = stack_loc.open_braces

		if set_indent:
			self.set_line_indent(absolute=stack_loc.pop_indent)
		# if the next token is another closing brace, self.prev_token stays None
		self.close_statement()
		return

	def push_composite_statement(self, token, indent=0, increment_nesting=1):
		# 'else' adds one nesting level:
		# else
		#     <statement>
		# 'else' 'if' adds only one nesting level:
		# else if ()
		#     <statement>
		# 'else' <LF> 'if' adds two nesting levels:
		# else
		#     if ()
		#          <statement>
		#
		self.composite_statement_token = token
		if token is IF_TOKEN \
				and self.composite_statement_stack \
				and self.composite_statement_stack[-1][0] is ELSE_TOKEN:
			# IF will replace ELSE on the composite stack
			else_token, prev_nesting_level = self.composite_statement_stack.pop(-1)
		else:
			prev_nesting_level = self.nesting_level
		self.set_line_indent(indent)
		composite_statement_state = (
			token,
			prev_nesting_level,
			)
		self.composite_statement_stack.append(composite_statement_state)

		self.nesting_level += increment_nesting
		self.statement_continuation = False
		return

	# pop stack:
	# If 'if' encountered, pop, set PENDING_ELSE nested state, bail out.
	# If state is DO_TOKEN, pop, set PENDING_WHILE nested state, bail out.
	def pop_composite_statement(self):
		while self.composite_statement_stack:
			composite_statement_state = self.composite_statement_stack.pop(-1)
			(
				token,
				self.nesting_level,
			) = composite_statement_state

			if token is IF_TOKEN:
				self.composite_statement_token = PENDING_ELSE_TOKEN
				return token
			if token is DO_TOKEN:
				self.composite_statement_token = PENDING_WHILE_TOKEN
				return token
		else:
			self.composite_statement_token = None

		return

	def process_opening_token(self, token):

		if token is CASE_TOKEN \
			or token is DEFAULT_TOKEN:
			self.context = token
			self.composite_statement_token = token
			self.set_line_indent(-1)
			return True
		elif token is TEMPLATE_TOKEN:
			self.context = token
			return True
		elif token is COLON:
			if self.context is CASE_TOKEN:
				self.statement_continuation = False
				self.expression_open = False
			self.context = None
			return True
		elif token is ALPHANUM_TOKEN \
			and self.context is None \
			and self.next_token is COLON:
			# This is a label
			self.context = COLON
			self.set_line_indent(absolute=0)
			return True

		elif token is ELSE_TOKEN:
			# begin a composite statement
			# No additional block indent if another composite statement
			# is on the same line with 'else'
			next_token = self.next_token
			keep_nesting = (
				next_token is IF_TOKEN
				or next_token is FOR_TOKEN
				or next_token is DO_TOKEN
				or next_token is WHILE_TOKEN
				or next_token is TRY_TOKEN
				or next_token is SWITCH_TOKEN)

			self.push_composite_statement(token,
						increment_nesting=not keep_nesting)
			return True

		if self.composite_statement_token is PENDING_ELSE_TOKEN:
			# Expected possible 'else' clause, but it didn't come.
			# Pop all nested 'if' statements,
			while self.composite_statement_stack and \
					self.composite_statement_stack[-1][0] is IF_TOKEN:
				_, self.nesting_level = self.composite_statement_stack.pop(-1)
			# and then pop all nested composite statements
			# until (and including) an 'if'.
			self.pop_composite_statement()

		if token is IF_TOKEN \
				or token is FOR_TOKEN \
				or token is DO_TOKEN \
				or token is TRY_TOKEN \
				or token is CATCH_TOKEN \
				or token is SWITCH_TOKEN:
			self.push_composite_statement(token)
			self.statement_open = True
			return True

		if token is DO_TOKEN:
			self.push_composite_statement(token)
			self.statement_open = True
			return True

		if token is WHILE_TOKEN:
			if self.composite_statement_token is PENDING_WHILE_TOKEN:
				token = DO_WHILE_TOKEN
			self.push_composite_statement(token)
			self.statement_open = True
			return True

		if token is NAMESPACE_TOKEN:
			self.push_composite_statement(token, increment_nesting=False)
			self.statement_open = True
			return True

		if token is RETURN_TOKEN:
			self.open_statement()
			self.assignment_open = True
			self.statement_continuation = True
			return True

		if token is TRY_TOKEN:
			self.push_composite_statement(token)
			self.statement_open = True
			return True

		return False

	def process_token(self, token):
		if type(token) is tuple:
			token, self.subtoken = token
		else:
			self.subtoken = None

		self.curr_token = token
		self.parse_token(token)
		if self.prev_token is None and self.curr_token is not None:
			self.set_line_indent()
		self.prev_token = self.curr_token
		return

	def parse_token(self, token):
		if not self.statement_open \
			and self.process_opening_token(token):
			return

		if token is BRACE_OPEN:
			self.expression_open = False
			if self.expression_stack or self.assignment_open:
				self.set_line_indent(0)
				self.push_expression_stack()
				return
			self.assignment_open = False
			if self.indent_case \
				and self.composite_statement_token is SWITCH_TOKEN:
				self.push_block(0)
				self.nesting_level += 1
			elif self.composite_statement_token is not None:
				self.push_block(0)
			else:
				self.push_block(1)
			return
		elif token is BRACE_CLOSE:
			if self.expression_stack:
				self.pop_expression_stack()
				self.set_line_indent(0)
				return
			if self.open_braces == 0:
				return
			# Closing statement
			if self.next_token is BRACE_CLOSE:
				# for multiple closing braces, set the line indent only after the last
				self.curr_token = None
			self.pop_block(set_indent=self.curr_token is not None)
			return
		elif token is SEMICOLON:
			# In 'for' statement, semicolons are used as separators
			if self.expression_stack and \
				self.expression_stack[-1].composite_statement_token is FOR_TOKEN:
				self.assignment_open = False
			else:
				self.open_statement()
				self.close_statement()
			return
		elif token is PAREN_OPEN:
			open_expression=True
			use_token_position = True
			parens_increment = 1
			indent_increment=None
			pop_statement_open = self.statement_open

			composite_statement_token = self.composite_statement_token
			if not self.expression_stack and \
					(composite_statement_token is IF_TOKEN \
					or composite_statement_token is FOR_TOKEN \
					or composite_statement_token is DO_TOKEN \
					or composite_statement_token is WHILE_TOKEN \
					or composite_statement_token is DO_WHILE_TOKEN \
					or composite_statement_token is CATCH_TOKEN \
					or composite_statement_token is SWITCH_TOKEN):

				self.statement_continuation = False
				pop_statement_open = False
				parens_increment = 0
				indent_increment=1
				if composite_statement_token is DO_WHILE_TOKEN:
					composite_statement_token = None
				elif composite_statement_token is FOR_TOKEN:
					pop_statement_open = False
				elif composite_statement_token is not IF_TOKEN:
					# For all other states (catch, switch) reset state in the parentheses
					self.composite_statement_token = None
					pop_statement_open = False
			else:
				parens_increment = 1
				if self.prev_token is ALPHANUM_TOKEN \
					or self.prev_token is ASSIGNMENT_OP \
					or self.prev_token is RETURN_TOKEN:
					open_expression=False
				else:
					self.set_line_indent()
					if self.open_parens:
						use_token_position = False
					elif self.nesting_level != 0:
						self.statement_continuation = self.assignment_open
					indent_increment=self.prev_token is not PAREN_OPEN

			self.push_expression_stack(
								composite_statement_token,	# pop after parentheses are closed
								pop_statement_open=pop_statement_open,
								use_token_position=use_token_position,
								parens_increment=parens_increment,
								indent_increment=indent_increment,
								assignment_open=False,
								statement_continuation=True,
								expression_open=open_expression)

			# Should be done after push_parentheses_stack
			self.assignment_open = False
			self.open_statement()
			return

		elif token is PAREN_CLOSE:
			stack_top = self.pop_expression_stack()

			if self.next_token is PAREN_CLOSE:
				# will set the indent for the next closing parenthesis
				self.curr_token = None
			elif stack_top is not None and (
				self.block_stack or
				self.composite_statement_stack or
				self.expression_stack):
				self.set_popped_stack_loc_indent(stack_top)
			else:
				# If on very top level, kick the parenthesis back
				self.set_line_indent(-1)
			return

		elif token is QUESTION:
			self.ternary_open += 1
		elif token is ASSIGNMENT_OP:
			if self.prev_token is OPERATOR:
				# Treat the following parentheses as a function arguments
				self.curr_token = ALPHANUM_TOKEN
				self.statement_open = True
				return
			if self.context is TEMPLATE_TOKEN:
				return
			self.assignment_open = True
			self.set_line_indent()
			if not self.expression_stack:
				self.statement_continuation = True
		elif token is OP:
			self.set_line_indent()
			if self.context is TEMPLATE_TOKEN and token == GREATER:
				self.close_statement()
			else:
				self.expression_open = True
				if not self.expression_stack:
					self.statement_continuation = True
		elif token is COLON:
			if self.ternary_open:
				self.ternary_open -= 1
			elif self.statement_open and not self.expression_stack:
				self.statement_continuation = False
				self.push_composite_statement(token, indent=1)
		elif token is ALPHANUM_TOKEN:
			self.set_line_indent()
			self.open_statement()
		elif token is COMMA and not self.expression_stack:
			self.assignment_open = False
			self.expression_open = False
			self.statement_continuation = False
		elif token is PRIVATE_TOKEN and self.next_token is COLON:
			# PRIVATE_TOKEN means 'private', 'public', 'protected'
			# This is a workaround for reformatting of Visual Studio generated MFC declarations:
			# A macro on previous line was not followed by a semicolon, thus a statement is still open
			self.close_statement()
			self.context = COLON
			self.set_line_indent(-1)
		else:
			self.open_statement()

		return

# The dictionary converts a character to a fixed token which can be tested with 'is'
operator_dict = {
	LESS: {
		EQUAL: (OP, LESS_EQUAL),
		LESS : {
			EQUAL : (ASSIGNMENT_OP, LEFT_SHIFT_EQUAL),
			None : (OP, LEFT_SHIFT),
		},
		None : (OP, LESS)
		},
	GREATER: {
		EQUAL: (OP, GREATER_EQUAL),
		GREATER : {
			EQUAL : (ASSIGNMENT_OP, RIGHT_SHIFT_EQUAL),
			None : (OP, RIGHT_SHIFT),
		},
		None : (OP, GREATER)
		},
	PLUS: {
		PLUS : (OP, PLUS_PLUS),
		EQUAL: (ASSIGNMENT_OP, PLUS_EQUAL),
		None : (OP, PLUS)
		},
	MINUS: {
		MINUS : (OP, MINUS_MINUS),
		EQUAL: (ASSIGNMENT_OP, MINUS_EQUAL),
		GREATER: (OP, ARROW),
		None : (OP, MINUS)
		},
	OR: {
		OR : (OP, OR_LOGICAL),
		EQUAL: (ASSIGNMENT_OP, OR_EQUAL),
		None : (OP, OR)
		},
	AND: {
		AND : (OP, AND_LOGICAL),
		EQUAL: (ASSIGNMENT_OP, AND_EQUAL),
		None : (OP, AND)
		},
	XOR: {
		EQUAL: (ASSIGNMENT_OP, XOR_EQUAL),
		None : (OP, XOR)
		},
	SLASH: {
		EQUAL: (ASSIGNMENT_OP, SLASH_EQUAL),
		None : (OP, SLASH)
		},
	BANG: {
		EQUAL: (OP, BANG_EQUAL),
		None : (OP, BANG)
		},
	ASTERISK: {
		EQUAL: (ASSIGNMENT_OP, ASTERISK_EQUAL),
		None : (OP, ASTERISK)
		},
	PERCENT: {
		EQUAL: (ASSIGNMENT_OP, PERCENT_EQUAL),
		None : (OP, PERCENT)
		},

	COLON: {
		COLON : COLONCOLON,
		None : COLON
		},
	PAREN_OPEN : PAREN_OPEN,
	PAREN_CLOSE : PAREN_CLOSE,
	BRACKET_OPEN : BRACKET_OPEN,
	BRACKET_CLOSE : BRACKET_CLOSE,
	SEMICOLON : SEMICOLON,
	BRACE_OPEN : BRACE_OPEN,
	BRACE_CLOSE : BRACE_CLOSE,
	QUESTION : QUESTION,
	DOT : DOT,
	NOT : (OP, NOT),
	EQUAL : {
		EQUAL: (OP, EQUAL_EQUAL),
		None: (ASSIGNMENT_OP, EQUAL),
	},
}
class pre_parsing_state:
	def __init__(self, config, log_handler):
		self.log_handler = log_handler
		self.format_slashslash_comments = config.format_comments.slashslash
		self.format_multiline_comments = config.format_comments.multiline
		self.format_oneline_comments = config.format_comments.oneline
		# Set to True when a line is joined to the next with a /* */ comment which crosses EOL
		self.comment_open = False
		self.comment_indent_ws:bytes = None	# Whitespaces in the first line of a multiline comment
		self.comment_indent_adjustment = 0	# Change of whitespace width in the first line of a multiline comment
		self.ends_with_open_comment = False
		self.starts_with_open_comment = False
		self.slash_slash_comment = False
		self.preprocessor_line = None
		self.if_stack = []
		self.non_ws_line = None
		self.non_ws_line_started = False
		# A preprocessor line can span multiple lines by joining them with a comment which spans lines
		# If a preprocessor line is running, we only parse comments, character literals and strings
		return

	def init_new_line(self, first_parse_line:parse_line, c_state:c_parser_state):
		self.slash_slash_comment = False
		self.empty = True
		self.line_num = first_parse_line.line_num
		self.non_ws_line = first_parse_line.non_ws_line
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
			c_state.init_new_line(first_parse_line)
		return

	def get_line_indent(self, c_state:c_parser_state):
		if self.starts_with_open_comment:
			if not (
				c_state.block_stack or
				c_state.composite_statement_stack or
				c_state.expression_stack):
				return LINE_INDENT_KEEP_CURRENT
			if not self.format_multiline_comments:
				return LINE_INDENT_KEEP_CURRENT
			if self.whitespace_width == 0:
				return LINE_INDENT_KEEP_CURRENT
			if self.comment_indent_ws is None \
				or not self.whitespaces.startswith(self.comment_indent_ws):
				return LINE_INDENT_KEEP_CURRENT
			if self.whitespace_width > self.comment_indent_adjustment:
				return self.whitespace_width - self.comment_indent_adjustment
			return 0

		if self.non_ws_line_started:
			# There's been non-comment tokens in the (joined by comments) line
			if not self.ends_with_open_comment:
				self.comment_indent_ws = None
			#Use c_state calculation
			return c_state.set_line_indent()
		# No meaningful data in this line so far, besides from comments.
		# If there are leading whitespaces, they are reformatted to the expected indent
		elif self.whitespace_width == 0:
			# Text (perhaps comment) starts from start of line. Keep it that way
			return 0
		elif not (
				c_state.block_stack or
				c_state.composite_statement_stack or
				c_state.expression_stack):
			# Oneline and '//' comments at the top level are not reindented
			return LINE_INDENT_KEEP_CURRENT
		elif self.slash_slash_comment:
			if not self.format_slashslash_comments:
				return LINE_INDENT_KEEP_CURRENT
			return c_state.set_line_indent()
		elif self.ends_with_open_comment:
			if not self.format_multiline_comments:
				return LINE_INDENT_KEEP_CURRENT
			return c_state.set_line_indent(0)
		# Oneline comment
		elif not self.format_oneline_comments:
			return LINE_INDENT_KEEP_CURRENT

		return c_state.set_line_indent(0)

	def tokenize_c_line(self, partial_lines:parse_partial_lines):

		ii = iter(partial_lines)
		next_c = next(ii, None)
		identifier_token = bytearray()

		while next_c[0] is not None:

			c, line, character_pos = next_c

			next_c = next(ii, None)

			if c is BACKSLASH_SEPARATOR:
				continue

			if c is EOL:
				yield None, None
				break

			if c == SPACE or c == TAB:
				continue

			if self.slash_slash_comment:
				# Need to process the whole line
				continue

			self.empty = False

			if self.comment_open:
				# Look for the next */
				if c == ASTERISK and next_c[0] == SLASH:
					next_c = next(ii, None)
					self.comment_open = False
				continue

			if c == SLASH:
				if next_c[0] == SLASH:
					self.slash_slash_comment = True
					continue
				if next_c[0] == ASTERISK:
					self.comment_open = True
					next_c = next(ii, None)
					continue

			if not self.non_ws_line_started:
				self.non_ws_line_started = True
				# Non white-space contents begins
				if self.preprocessor_line is None and c == POUND:
					self.preprocessor_line = b'#'
					continue

			token_position = character_pos

			if c == WIDE_STRING_PREFIX and \
				(next_c[0] == DOUBLE_QUOTE or next_c[0] == SINGLE_QUOTE):
				c, line = next_c
				next_c = next(ii, None)

			if c == DOUBLE_QUOTE or c == SINGLE_QUOTE:
				cc = c
				while next_c[0] is not EOL:
					c, line, _ = next_c
					if line is not None:
						line.indent = LINE_INDENT_KEEP_CURRENT_NO_RETAB
					next_c = next(ii, None)
					if cc == c:
						break
					if c == BACKSLASH:
						next_c = next(ii, None)
					continue

				if c == DOUBLE_QUOTE:
					yield STRING_LITERAL, token_position
				else:
					yield QUOTED_LITERAL, token_position

				continue

			if is_alphanumeric(c):
				if self.preprocessor_line:
					if self.preprocessor_line != b'#':
						# Don't care about other alphanumeric tokens in a preprocessor line
						continue
					# Beginning of the very first token after pound sign
					identifier_token += b'#'

				while 1:
					identifier_token.append(c)
					c, line, _ = next_c
					if not is_alphanumeric(c):
						break
					next_c = next(ii, None)
					continue

				token = bytes(identifier_token)
				identifier_token.clear()
				if self.preprocessor_line is None:
					token = decode_alphanumeric_token(token)
				else:
					token = decode_preprocessor_token(token)
				yield token, token_position
				continue

			if self.preprocessor_line is not None:
				# Only parse tokens (besides from the very first) in non-preprocessor line
				continue

			# Note that we don't yield 'c' itself, but the
			# global constant, to be able to match it by 'is' operator
			token = operator_dict.get(c, c)
			while type(token) is dict:
				c, line, _ = next_c
				if c not in token:
					token = token[None]
					break

				token = token[c]
				next_c = next(ii, None)
				continue

			yield token, token_position
			continue

		return

	def finalize_lines(self, lines_to_write, c_state:c_parser_state):
		self.ends_with_open_comment = self.comment_open
		if self.preprocessor_line is not None:
			if self.preprocessor_line is IF_LINE \
					or self.preprocessor_line is IFDEF_LINE \
					or self.preprocessor_line is IFNDEF_LINE:
				# Save parsing state
				ps = c_state.save_state(self.non_ws_line)
				self.if_stack.append(ps)
				return

			if not self.if_stack:
				return
			if self.preprocessor_line is ELIF_LINE \
					or self.preprocessor_line is ELSE_LINE:
				prev_ps = self.if_stack.pop(-1)
				# Save new parsing state
				ps = c_state.save_state(self.non_ws_line,
							prev_ps.ignore_nesting_change, prev_ps.restore_c_state)
				self.if_stack.append(ps)
				c_state.restore_state(prev_ps)
				return

			if self.preprocessor_line is ENDIF_LINE:
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
	fix_last_eol = config.fix_last_eol

	if not (fix_cr_eol or fix_last_eol):
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
			if fix_last_eol and not line.endswith(b'\n'):
				line += b'\n'
			yield line
		else:
			if line.endswith(b'\r'):
				# Last line in the file ends with a single CR
				line += b'\n'
			elif fix_last_eol and not line.endswith(b'\n'):
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
							fix_last_eol=False,
							indent_case=False,
							format_comments = SimpleNamespace(
								oneline=True, slashslash=True, multiline=True),

							reindent_continuation = SimpleNamespace(
								any=True,
								extend=False,
								max_to_parenthesis=64,		# Max abs parentheses position
								to_parenthesis=False),
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
		if not partial_lines.lines[-1].eol:
			log_handler('File ends without EOL character')

		# Needs to be done before getting next_token
		first_line = partial_lines.lines[0]
		pp_state.init_new_line(first_line, c_state)

		token_iter = pp_state.tokenize_c_line(partial_lines)

		# We have the next token for lookahead
		token = None
		next_token = next(token_iter, None)

		while next_token is not None:
			token, c_state.token_position = next_token

			next_token = next(token_iter, None)	# Never None

			if pp_state.preprocessor_line:
				if token is PREPROCESSOR_LINE or \
						token is IF_LINE or \
						token is ELSE_LINE or \
						token is ELIF_LINE or \
						token is IFDEF_LINE or \
						token is IFNDEF_LINE or \
						token is DEFINE_LINE or \
						token is ENDIF_LINE:
					pp_state.preprocessor_line = token
				# Consume all tokens, including BACKSLASH_SEPARATOR
				continue

			if token is None:
				break
			else:
				c_state.next_token, c_state.next_token_position = next_token

			c_state.process_token(token)
			continue

		yield partial_lines.lines, pp_state, c_state
		continue

	return

def fix_file_lines(in_fd, config):

	for line in read_and_fix_lines(in_fd, config):
		p = parse_line(line, config)
		if config.retab_only:
			line_indent = LINE_INDENT_KEEP_CURRENT
		else:
			line_indent = LINE_INDENT_KEEP_CURRENT_NO_RETAB
		yield p.make_line(line_indent)
	return

def format_data(data, format_spec, error_handler=None):
	if not format_spec.skip_indent_format and not format_spec.retab_only:
		yield from format_c_file(io.BytesIO(data), format_spec, error_handler)
	elif format_spec.trim_trailing_whitespace or format_spec.fix_eol or format_spec.retab_only:
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
	parser.add_argument("--retab-only", default=False, action='store_true',
					help="Only convert existing indents to tabs or spaces.")
	parser.add_argument("--fix-eols", default=False, action='store_true',
					help="Fix lonely carriage returns into line feed characters. Git by default doesn't do that.")
	parser.add_argument("--fix-last-eol", default=False, action='store_true',
					help="Fix last line without End Of Line character(s).")
	parser.add_argument("--indent-case", default=False, action='store_true',
					help="Indent case labels and code witin switch blocks. By default, case labels are at same indent level as the case statement")
	parser.add_argument("--no-indent-continuation", dest='continuation', default=None, action='store_const', const='none',
					help="Do not reindent statement continuation lines")
	parser.add_argument("--continuation", choices=('none', 'smart', 'extend'),
					help="option for reformatting the statement continuation lines to the first parenthesis or other notable position")

	class format_comments_action(argparse.Action):

		def __call__(self, parser, namespace, value, option_string):
			format_comments = namespace.format_comments
			if format_comments is None:
				format_comments = SimpleNamespace(
					oneline=False, slashslash=False, multiline=False)
				namespace.format_comments = format_comments
			if value is None:
				format_comments.oneline = True
				format_comments.slashslash = True
				format_comments.multiline = False
				return

			for f in value.split(','):
				if f == 'slashslash':
					format_comments.slashslash = True
				elif f == 'oneline':
					format_comments.oneline = True
				elif f == 'multiline':
					format_comments.multiline = True
				elif f == 'all':
					format_comments.slashslash = True
					format_comments.oneline = True
					format_comments.multiline = True
				elif f == 'none':
					format_comments.slashslash = False
					format_comments.oneline = False
					format_comments.multiline = False
				else:
					raise argparse.ArgumentError(
						"argument --format-comments: invalid choice: '%s'"
						"(choose from 'all', 'none', 'slashslash', 'oneline', 'multiline')")
				continue
			return

	parser.add_argument("--format-comments", action=format_comments_action, nargs='?',
					metavar='none|all|slashslash|oneline|multiline',
					help="Reformat comment indents.")

	options = parser.parse_args()

	if options.format_comments is None:
		options.format_comments = SimpleNamespace(oneline=True, slashslash=True, multiline=True)

	file_list = get_file_list(options.infile, options.current_dir, options.out_file)

	if not file_list:
		return parser.print_usage(sys.stderr)

	indent_continuation = SimpleNamespace(
		any=options.continuation != 'none',
		extend=options.continuation == 'extend',
		max_to_parenthesis=64,		# Max abs parentheses position
		smart=options.continuation == 'smart')

	conf = SimpleNamespace(
		tab_size = options.tab_size,
		skip_indent_format = options.style == 'keep',
		indent = options.indent_size,
		retab_only = options.retab_only,
		trim_trailing_whitespace = options.trim_whitespace,
		fix_eol = options.fix_eols,
		fix_last_eol = options.fix_last_eol,
		indent_case = options.indent_case,
		reindent_continuation = indent_continuation,
		format_comments=options.format_comments,
		tabs = options.style == 'tabs')

	for file in file_list:
		if not conf.retab_only and (conf.skip_indent_format \
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
