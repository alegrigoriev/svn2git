# parse-svn-dump: Subversion dump reader

This Python program allows you to read and analyze a dump of Subversion (SVN) repository.

To create a dump of an SVN repository, use the following SVN command:

`svnrdump dump <URL>/<directory> -f <output file> [-r <revisions>] [--incremental]`

`<URL>` refers to the URL of the repository on a server, or on the local computer.
An  `-r <revision>` command line option allows you to specify a range of revisions to dump.
`--incremental` option tells to create an incremental dump which contains only diffs from the revision preceding the beginning of the revision range specified by `-r` option.
If you are creating a sequence of partial dumps by running `svnrdump` several times with sequential revision ranges, you want to specify `--incremental` option. 
The option doesn't affect the dump data, if you start from the very first revision.

See SVN manual for more details.

Running the program
-------------------

The program is invoked by the following command line:

`python parse-svn-dump.py <input file> [<options>]`

The following command line options are supported:

`--version`
- show program version.

`--log <log file>`
- write log to a file. By default, the log is sent to the standard output.

`--verify-data-hash` (or `-V`)
- Verify integrity of the SVN dump file by checking the hashes.
