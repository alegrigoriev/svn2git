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

if sys.version_info < (3, 9):
	sys.exit("parse-svn-dump: This package requires Python 3.9+")

from exceptions import Exception_svn_parse
from svn_dump_reader import svn_dump_reader, print_stats as svn_dump_stats
from history_reader import load_history

def main():
	in_file = sys.stdin.buffer

	try:
		load_history(svn_dump_reader(in_file), sys.stdout)
	finally:
		svn_dump_stats(sys.stdout)

	return 0

if __name__ == "__main__":
	try:
		sys.exit(main())
	except Exception_svn_parse as ex:
		print("ERROR: %s" % ex.strerror, file=sys.stderr)
		sys.exit(128)
	except KeyboardInterrupt:
		# silent abort
		sys.exit(130)
