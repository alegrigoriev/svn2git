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

import re

def rev_in_ranges(ranges, rev: int):
	for rev_range in ranges:
		if rev >= rev_range[0] and rev <= rev_range[1]:
			return True
	return False

def sort_ranges(ranges):
	# sort by start of range
	if not ranges:
		return ranges
	ranges = sorted(ranges)

	prev_start = None
	prev_end= None
	result = []

	for (start, end) in ranges:
		if prev_end is not None and end <= prev_end:
			continue
		if prev_end is not None and start <= prev_end + 1:
			result[-1] = (prev_start, end)
		else:
			prev_start = start
			result.append( (start, end) )
		prev_end = end

	return result

def ranges_to_str(ranges):
	return ','.join(str(t[0]) if t[0] == t[1] else ('%d,%d' % (t[0], t[1])) if t[0] + 1 == t[1] else ('%d-%d' % (t[0], t[1])) for t in ranges)

def combine_ranges(first, second):
	return sort_ranges(first + second)

def subtract_ranges(current_revs, prev_revs):
	if not prev_revs:
		return current_revs

	result = []
	for (start, end) in current_revs:
		# find if the range appears in prev_revs. Cut all prev_revs out of it
		for (sub_start, sub_end) in prev_revs:
			if sub_start > end:
				break
			if sub_end < start:
				continue
			# exclusion range within revision range
			if sub_start <= start:
				start = sub_end + 1
				continue

			result.append( (start, sub_start - 1) )
			start = sub_end + 1

		if end >= start:
			result.append( (start, end) )

	return result

def str_to_ranges(src:str):
	ranges = []
	if not src:
		return ranges

	for s in src.split(','):
		m = re.fullmatch(r'(\d+)(?:-(\d+))?', s)
		if not m:
			raise ValueError()
		start = m[1]
		if m[2]:
			end = m[2]
		else:
			end = start

		ranges.append( (int(start), int(end)) )

	return sort_ranges(ranges)

def mergeinfo_str_to_ranges(src:str):
	ranges = []
	if not src:
		return ranges

	for s in src.split(','):
		m = re.fullmatch(r'(\d+)(?:-(\d+))?(\*)?', s)
		if not m:
			raise ValueError()
		start = m[1]
		if m[2]:
			end = m[2]
		else:
			end = start

		# m[3] is not empty if the range is not inheritable TODO
		ranges.append( (int(start), int(end)) )

	return ranges
