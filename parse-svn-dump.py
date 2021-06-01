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

from exceptions import Exception_svn_parse, Exception_history_parse
from svn_dump_reader import svn_dump_reader, print_stats as svn_dump_stats
from history_reader import history_reader

def main():
	import argparse
	parser = argparse.ArgumentParser(description="Parse SVN dump stream and print results", allow_abbrev=False)
	parser.add_argument('--version', action='version', version='%(prog)s 0.1')
	parser.add_argument(dest='in_files', help="input dump file name. Use multiple arguments for partial files", nargs='+')
	parser.add_argument("--log", dest='log_file', help="Logfile destination; default to stdout")
	parser.add_argument("--verbose", "-v", dest='verbose', help="Log verbosity:",
						choices=['dump'],
						action='append', nargs='?', const='dump', default=[])
	parser.add_argument("--end-revision", "-e", metavar='REV', dest='end_revision', help="Revision to stop the input file processing")
	group = parser.add_argument_group()
	group.add_argument("--quiet", '-q', help="Suppress progress indication", action='store_true')
	group.add_argument("--progress", nargs='?', help="Forces progress indication when not detected as on terminal, and optionally sets the update period in seconds",
					type=float, action='store', const='1.', default='1.' if sys.stderr.isatty() else None)
	parser.add_argument("--verify-data-hash", '-V', dest='verify_data_hash', help="Verify data SHA1 and/or MD5 hash", default=False, action='store_true')

	options = parser.parse_args();

	if options.log_file:
		options.log_file = open(options.log_file, 'wt', 0x100000, encoding='utf=8')
	else:
		options.log_file = sys.stdout
	log_file = options.log_file

	# If -v specified without value, the const list value is assigned as a list item. Extract it to be the part of list instead
	if options.verbose and type(options.verbose[0]) is list:
		o = options.verbose.pop(0)
		options.verbose += o

	options.log_dump = 'dump' in options.verbose

	history = history_reader(options)

	try:
		history.load(svn_dump_reader(*options.in_files))
	finally:
		svn_dump_stats(log_file)
		log_file.close()

	return 0

if __name__ == "__main__":
	try:
		sys.exit(main())
	except FileNotFoundError as fnf:
		print("ERROR: %s: %s" % (fnf.strerror, fnf.filename), file=sys.stderr)
		sys.exit(1)
	except Exception_svn_parse as ex:
		print("ERROR: %s" % ex.strerror, file=sys.stderr)
		sys.exit(128)
	except Exception_history_parse as ex:
		print("ERROR: %s" % ex.strerror, file=sys.stderr)
		sys.exit(128)
	except KeyboardInterrupt:
		# silent abort
		sys.exit(130)
