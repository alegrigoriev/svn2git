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

`python parse-svn-dump.py <input files...> [<options>]`

Multiple input files can be specified in the command line.
This allows to use incremental dumps of your SVN repository.
The revision numbers in consecutive files should be in a contiguous sequence.
They cannot go back, but can skip some numbers.
This is useful when extracting dumps from a corrupted remote repository
(subversion on apache.org).

The following command line options are supported:

`--version`
- show program version.

`--log <log file>`
- write log to a file. By default, the log is sent to the standard output.

`--end-revision <REV>`
- makes the dump stop after the specified revision number.

`--quiet`
- suppress progress indication (number of revisions processed, time elapsed).
By default, the progress indication is active on a console,
but is suppressed if the standard error output is not recognized as console.
If you don't want progress indication on the console, specify `--quiet` command line option.

`--progress[=<period>]`
- force progress indication, even if the standard error output is not recognized as console,
and optionally set the update period in seconds as a floating point number.
For example, `--progress=0.1` sets the progress update period 100 ms.
The default update period is 1 second.

`--verbose={dump|revs|all|dump_all}`
- dump additional information to the log file.

	`--verbose=dump`
	- dump revisions to the log file.

	`--verbose=revs`
	- log the difference from each previous revision, in form of added, deleted and modified files and attributes.
This doesn't include file diffs. Note that a directory copy operation will be shown as all files added.

	`--verbose=dump_all`
	- dump all revisions, even empty revisions without any change operations.
Such empty revisions can be issued if you dump a subdirectory of an SVN repository.
By default, `--verbose=dump` and `--verbose=all` don't dump empty revisions.

	`--verbose=all`
	- same as `--verbose=dump --verbose=revs`

`--verify-data-hash` (or `-V`)
- Verify integrity of the SVN dump file by checking the hashes.
