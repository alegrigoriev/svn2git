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
	sys.exit("svn-to-git: This package requires Python 3.9+")

from exceptions import Exception_svn_parse, Exception_history_parse, Exception_cfg_parse
from svn_dump_reader import svn_dump_reader, print_stats as svn_dump_stats
from history_reader import history_reader, print_diff as print_history_diff
from project_tree import project_history_tree, print_stats as project_tree_stats

def main():
	import argparse
	parser = argparse.ArgumentParser(description="Parse SVN dump stream and optionally convert it to Git repo(s)", allow_abbrev=False)
	parser.add_argument('--version', action='version', version='%(prog)s 0.1')
	parser.add_argument(dest='in_files', help="input dump file name. Use multiple arguments for partial files", nargs='+')
	parser.add_argument("--log", dest='log_file', help="Logfile destination; default to stdout")
	parser.add_argument("--verbose", "-v", dest='verbose', help="Log verbosity:",
						choices=['dump', 'dump_all', 'revs', 'commits', 'merges', 'merges-verbose', 'all'],
						action='append', nargs='?', const=['dump', 'commits'], default=[])
	parser.add_argument("--end-revision", "-e", metavar='REV', dest='end_revision', help="Revision to stop the input file processing")
	group = parser.add_argument_group()
	group.add_argument("--quiet", '-q', help="Suppress progress indication", action='store_true')
	group.add_argument("--progress", nargs='?', help="Forces progress indication when not detected as on terminal, and optionally sets the update period in seconds",
					type=float, action='store', const='1.', default='1.' if sys.stderr.isatty() else None)
	parser.add_argument("--compare-to", "-C", dest='compare_to', help="Single revision SVN dump file to compare the final tree against")
	parser.add_argument("--verify-data-hash", '-V', dest='verify_data_hash', help="Verify data SHA1 and/or MD5 hash", default=False, action='store_true')
	parser.add_argument("--config", "-c", help="XML file to configure conversion to Git repository")
	parser.add_argument("--trunk", help="Main branch directory name , default 'trunk'", default='trunk')
	parser.add_argument("--branches", help="Branches directory name, default 'branches'", default='branches')
	parser.add_argument("--user-branches", help="Names of user-specific branch directories, default ['users/branches', 'branches/users']",
					action='append', default=['users/branches', 'branches/users'])
	parser.add_argument("--tags", help="Tags directory name, default 'tags'", default='tags')
	parser.add_argument("--map-trunk-to", dest='map_trunk_to', help="Branch name for trunk in Git repository, default 'main'", default='main')
	parser.add_argument("--no-default-config", dest='use_default_config', default=True, action='store_false',
					help="Don't use default mappings (**/trunk, **/branches/*, **/tags/*). The mappings need to be provided in a config file, instead")
	parser.add_argument("--path-filter", dest='path_filter', default=[],
					help="Process only selected paths. The option value is Git-style globspec", action='append')
	parser.add_argument("--project", dest='project_filter', default=[],
					help="Process only selected projects. The option value is Git-style globspec", action='append')
	parser.add_argument("--target-repository", dest='target_repo', help="Target Git repository to write the conversion result")
	parser.add_argument("--decorate-commit-message", help="Add taglines to the commit message:", choices=['revision-id'],
						action='append', default=[])

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

	options.log_dump = 'dump' in options.verbose or 'all' in options.verbose
	# dump_all is not included in --verbose=all
	options.log_dump_all = 'dump_all' in options.verbose
	options.log_revs = 'revs' in options.verbose or 'all' in options.verbose
	options.log_commits = 'commits' in options.verbose or 'all' in options.verbose
	options.log_merges = 'merges' in options.verbose or 'all' in options.verbose
	options.log_merges_verbose = 'merges-verbose' in options.verbose

	options.decorate_revision_id = 'revision-id' in options.decorate_commit_message

	project_tree = project_history_tree(options)

	try:
		project_tree.load(svn_dump_reader(*options.in_files))

		project_tree.print_unmapped_directories(log_file)

		if options.compare_to:
			compare_history = history_reader().load(svn_dump_reader(options.compare_to))
			print("Comparing with rev file " + options.compare_to, file=log_file)
			print_history_diff([*compare_history.head_tree().compare(project_tree.head_tree())], log_file)

	finally:
		svn_dump_stats(log_file)
		project_tree_stats(log_file)
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
	except Exception_cfg_parse as ex:
		print("ERROR: %s" % ex.strerror, file=sys.stderr)
		sys.exit(128)
	except KeyboardInterrupt:
		# silent abort
		sys.exit(130)
