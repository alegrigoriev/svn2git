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

`--config <XML config file>` (or `-c <XML config file>`)
- specify the configuration file for mapping SVN directories to branches.
See [XML configuration file](#xml-config-file) chapter.

`--log <log file>`
- write log to a file. By default, the log is sent to the standard output.

`--end-revision <REV>`
- makes the dump stop after the specified revision number.

`--compare-to <dump file 2>` (or `-C <dump file 2>`)
- compare the reconstructed tree to the tree from the specified dump file.
This is a debug option, to verify that the tree reconstruction works correctly.

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

`--trunk <trunk directory name>`
- use this directory name as the trunk branch. The default is **trunk**.
This value is also assigned to **$Trunk** variable to use for substitutions in the XML config file.

`--branches <branches directory name>`
- use this directory name as the root directory for branches. The default is **branches**.
This value is also assigned to **$Branches** variable to use for substitutions in the XML config file.

`--user-branches <user branches directory name>`
- use this directory name as the root directory for branches. The default is **users/branches,branches/users**.
This value is also assigned to **$UserBranches** variable to use for substitutions in the XML config file.

`--tags <tags directory name>`
- use this directory name as the root directory for tags. The default is **tags**.
This value is also assigned to **$Tags** variable to use for substitutions in the XML config file.

`--map-trunk-to <main branch name in Git>`
- the main branch name in Git. The trunk directory will be mapped to this branch name.
The default is **main**. This value is also assigned to **$MapTrunkTo** variable to use for substitutions in the XML config file.

`--no-default-config`
- don't use default mappings for branches and tags. This option doesn't affect default variable assignments.

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

`--path-filter <path filter glob>`
- selects SVN paths to filter for. This option can appear multiple times. See [Path filtering](#path-filtering).

`--project <project name filter>`
- selects projects to process. This option can appear multiple times. See [Project filtering](#project-filtering).

XML configuration file{#xml-config-file}
======================

Mapping of SVN repo directories to Git branches, and other settings, is described by an XML configuration file.
This file is specified by `--config` command line option.

The file consists of the root node `<Projects>`, which contains a single section `<Default>` and a number of sub-sections `<Project>`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<Projects xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation=". svn-to-git.xsd">
	<Default>
		<!-- default settings go here -->
	</Default>
	<Project Name="*" Path="*">
		<!-- per-project settings go here -->
	</Project>
</Projects>
```

`xsi:schemaLocation` attribute refers to `svn-to-git.xsd` schema file provided with this repository,
which can be used to validate the XML file in some editors, for example, in Microsoft Visual Studio.

Wildcard (glob) specifications in the config file{#config-file-wildcard}
-------------------------------------------------

Paths and other path-like values in the configuration file can contain wildcard (glob) characters.
In general, these wildcards follow Unix/Git conventions. The following wildcards are recognized:

'`?`' - matches any character;

'`*`' - matches a sequence of any characters, except for slash '`/`'. The matched sequence can be empty.

'`/*/`' - matches a non-empty sequence of any (except for slash '`/`') characters between two slashes.

'`*/`' in the beginning of a path - matches a non-empty sequence of any (except for slash '`/`') characters before the first slash.

'`**`' - matches a sequence of any characters, _including_ slashes '`/`', **or** an empty string.

'`**/`' - matches a sequence of any characters, _including_ slashes '`/`', ending with a slash '`/`', **or** an empty string.

`{<match1>,...}` - matches one of the comma-separated patterns (each of those patterns can also contain wildcards).

Note that `[range...]` character range Unix glob specification is not supported.

As in Git, a glob specification which matches a single path component (with or without a trailing slash) matches such a component at any position in the path.
If a trailing slash is present, only directory-like components can match.
If there's no trailing slash, both directory- and file-like components can match the given glob specification. Thus, a single '`*`' wildcard matches any filename.
If a glob specification can match multiple path components, it's assumed it begins with a slash '`/`', meaning the match starts with the beginning of the path.

In many places, multiple wildcard specifications can be present, separated by a semicolon '`;`'.
They are tested one by one, until one matches.
In such a sequence, a negative wildcard can be present, prefixed with a bang character '`!`'.
If a negative wildcard matches, the whole sequence is considered no-match.
You can use such negative wildcards to carve exceptions from a wider wildcard.
If all present wildcards are negative, and none of them matches, this considered a positive match, as if there was a "`**`" match all specification in the end.

Variable substitutions in the config file{#variable-substitutions}
-----------------------------------------

You can assign a value to a variable, and have that value substituted whenever a string contains a reference to that variable.

The assignment is done by `<Vars>` section, which can appear under `<Default>` and `<Project>` sections. It has the following format:

```
		<Vars>
			<variable_name>value</variable_name>
		</Vars>
```

The following default variables are preset:

```xml
		<Vars>
			<Trunk>trunk</Trunk>
			<Branches>branches</Branches>
			<UserBranches>users/branches;branches/users</UserBranches>
			<Tags>tags</Tags>
			<MapTrunkTo>main</MapTrunkTo>
		</Vars>
```

They can be overridden explicitly in `<Default>` and `<Project>` sections,
and/or by the command line options `--trunk`, `--branches`, `--user-branches`, `--tags`, `--map-trunk-to`.

For the variable substitution purposes, the sections are processed in order,
except for the specifications injected from `<Default>` section into `<Project>`.
All `<Vars>` definitions from `<Default>` are processed before all sections in `<Project>`.

For substitution, you refer to a variable as `$<variable name>`,
for example `$Trunk`, or `${<variable name>}`, for example `${Branches}`.
Another acceptable form is `$(<variable name>)`, for example `$(UserBranches)`.
You have to use the form with braces or parentheses
when you need to follow it with an alphabetical character, such as `${MapTrunkTo}1`.

Note that if a variable value is a list of semicolon-separated strings, like `users/branches;branches/users`,
its substitution will match one of those strings,
as if they were in a `{}` wildcard, like `{users/branches,branches/users}`.

A variable definition can refer to other variables. Circular substitutions are not allowed.

The variable substitution is done when the XML config sections are read.
When another `<Vars>` section is encountered, it affects the sections that follow it.

Ref character substitution{#ref-character-substitution}
--------------------------

Certain characters are valid in SVN directory names, but not allowed in Git refnames.
The program allows to map invalid characters to allowed ones. The remapping is specified by `<Replace>` specification:

```xml
		<Replace>
			<Chars>source character</Chars>
			<With>replace with character</With>
		</Replace>
```

This specification is allowed in `<Default>` and `<Project>` sections.
All `<Replace>` definitions from `<Default>` are processed before all sections in `<Project>`.

Example:

```xml
		<Replace>
			<Chars> </Chars>
			<With>_</With>
		</Replace>
```

This will replace spaces with underscores.

`<Default>` section{#default-section}
---------------

A configuration file can contain zero or one `<Default>` section under the root node.
This section contains mappings and variable definitions to be used as defaults for all projects.
In absence of `<Project>` sections, the `<Default>` section is used as a default project.

`<Default>` section is merged into beginning of each `<Project>` section,
except for `<MapPath>` specifications,
which are merged _after_ the end of each `<Project>` section.

`InheritDefault="No"` attribute in the `<Default>` section header suppresses
inheritance from the hardcoded configuration.

`InheritDefaultMappings="No"` suppresses inheritance of default `<MapPath>`
mappings.

`<Vars>` and `<Replace>` specifications are always inherited from the hardcoded defaults
or passed from the command line.

`<Project>` section{#project-section}
---------------

A configuration file can contain zero or more `<Project>` sections under the root node.
This section isolates mappings, variable definitions, and other setting to be used together.

A `<Project>` section can have optional `Name` and `Path` attributes.

If supplied, `Name` attribute values must be unique: two `<Project>` sections cannot have same name.

The `Path` value filters the SVN directories to be processed with this `<Project>`.
Its value can be one or more wildcards (glob) specifications, separated by semicolons.

`InheritDefault="No"` attribute in a `<Project>` or `<Default>` section suppresses
inheritance from its default (from hardcoded config or from `<Default>`
section).

`InheritDefaultMappings="No"` suppresses inheritance of default `<MapPath>`
mappings.

`<Vars>` and `<Replace>` specifications are always inherited from the hardcoded defaults
or passed from the command line. Only their overrides in `<Default>` section will get ignored.

`<Project>` sections with `ExplicitOnly="Yes"` attribute are only used if explicitly selected
by `--project` command line option.

Path to Ref mapping{#path-mapping}
-------------------

Subversion represents branches and tags as directories in the repository tree.
Note that they are just regular directories, without any special flag or attribute.
A special meaning assigned to `trunk` or to directories under `branches` and `tags` is just a convention.

Thus, the program needs to be told how to map directories to Git refs.

This program provides a default mapping which covers the most typical SVN repository organization. By default, it maps:

`**/$Trunk` to `refs/heads/**/$MapTrunkTo`

`**/$UserBranches/*/*` to `refs/heads/**/users/*/*`

Note that `$UserBranches` by default matches `users/branches` and `branches/users`.
One trailing path component matches an user name, and the next path component matches the branch name.
Thus, the Git branch name will have format `users/<username>/<branch>`.

`**/$Branches/*` to `refs/heads/**/*`

`**/$Tags/*` to `refs/tags/**/*`

By virtue of `**/` matching any (or none) number of directory levels,
this default mapping support both single- and multiple-projects repository structure.

With single project structure, `trunk`, `branches` and `tags` directories are at the root directory level of an SVN repository,
and they are mapped to `$MapTrunkTo`, branches and tags at the corresponding `refs` directory. 

With multiple projects repository, `trunk`, `branches` and `tags` directories at the subdirectories are mapped to corresponding refs under same subdirectories in `refs/heads` and `refs/tags`.
For example, `Proj1/trunk` will be mapped to `refs/heads/Proj1/$MapTrunkTo`, which then gets substituted as `refs/heads/Proj1/main`.

Non-default mapping allows to handle more complex cases.

You can map a directory matching the specified pattern, into a specific Git ref,
built by substitution from the original directory path. This is done by `<MapPath>` sections in `<Project>` or `<Default>` sections:

```xml
	<Project>
		<MapPath>
			<Path>path matching specification</Path>
			<Refname>ref substitution string</Refname>
			<!-- optional: -->
			<RevisionRef>revision ref substitution</RevisionRef>
		</MapPath>
	</Project>
```

Here, `<Path>` is a glob (wildcard) match specification to match the beginning of SVN directory path,
`<Refname>` is the refname substitution specification to make Git branch refname for this directory,
and the optional `<RevisionRef>` substitution specification makes a root for revision refs for commits made on this directory.

The program replaces special variables and specifications in `ref substitution string`
with strings matching the wildcard specifications in `path matching specification`.
During the pattern match, each explicit wildcard specification, such as '`?`', '`*`', '`**`', '`{pattern...}`',
assigns a value to a numbered variable `$1`, `$2`, etc.
The substitution string can refer to those variables as `$1`, `$2`, or as `${1}`, `$(2)`, etc.
Explicit brackets or parentheses are required if the variable occurrence has to be followed by a digit.
If the substitutions are in the same order as original wildcards, you can also refer to them as '`*`', '`**`'.

Note that you can only refer to wildcards in the initial match specification string,
not to wildcards inserted to the match specification through variable substitution.

Every time a new directory is added into an SVN repository tree,
the program tries to map its path into a symbolic reference AKA ref ("branch").

`<MapPath>` definitions are processed in their order in the config file in each `<Project>`.
First `<Project>` definitions are processed, then definitions from `<Default>`,
and then default mappings described above (unless they are suppressed by `--no-default-config` command line option).

The first `<MapPath>` with `<Path>` matching the beginning of the directory path will define which Git "branch" this directory belongs to.
The rest of the path will be a subdirectory in the branch worktree.
For example, an SVN directory `/branches/feature1/includes/` will be matched by `<Path>**/$Branches/*</Path>`
and the specification `<Refname>refs/heads/**/*</Refname>` will map it to ref `refs/heads/feature1` worktree directory `/includes/`.

The target refname in `<Refname>` specification is assumed to begin with `refs/` prefix.
If the `refs/` prefix is not present, it's implicitly added.

If a refname produced for a directory collides with a refname for a different directory,
the program will try to create an unique name by appending `__<number>` to it.

If `<Refname>` specification is omitted, this directory and all its subdirectories are explicitly unmapped from creating a branch. 

The program creates a special ref for each commit it makes, to map SVN revisions to Git commits.
An optional `<RevisionRef>` specification defines how the revision ref name root is formatted.
Without `<RevisionRef>` specification, an implicit mapping will make
refnames for branches (Git ref matching `refs/heads/<branch name>`) as `refs/revisions/<branch name>/r<rev number>`,
and for tag branches (Git ref matching `refs/tags/<tag name>`) as `refs/revisions/<branch name>/r<rev number>`.

Path filtering{#path-filtering}
--------------
`--path-filter <path glob specification>` command line option allows to select directories to process,
while ignoring other directories.
`path glob specification` supplied in the option is matched against beginning of SVN paths.
For example, `--path-filter /project1` option will process everything under `/project1` directory.

Multiple `--path-filter` options can be supplied in the command line.
Each option value can also contain multiple glob specifications, separated by commas '`,`'.

A (combined) path list can also contain a *negative* filter, which starts with '`!`' character.
Note that in *bash* command line, '`!`' character needs to be single-quoted as "`'!'`"
to prevent history expansion.
This also means it has to be outside of double quotes:

`--path-filter '!'"quoted path"`

SVN directories not matching `--path-filter` options will also be excluded from SVN revision dump in the log file.

Project filtering{#project-filtering}
-----------------

You can select to process only some projects - enable only selected `<Project>` sections in the XML configuration file.
Projects to process are selected by specifying `--project <project name filter>` option(s) in the command line.

Multiple `--project` options can be supplied in the command line.
The option value can contain multiple project name filters, separated by commas.

If a filter is prefixed with an exclamation mark '`!`',
this pattern excludes projects (negative match).
Note that in *bash* command line, '`!`' character needs to be single-quoted as "`'!'`"
to prevent history expansion:

`--project '!'<excluded project pattern>`

SVN history tracking{#svn-history-tracking}
----------------

SVN tracks history of each file by copies and merges.
A new branch or a tag starts by SVN copying a root directory of its parent branch to a new directory,
typically under `/branches/` or `/tags/` directory.
Note that SVN copy is a special internal operation, different from copying a directory in the working directory and committing it.

SVN doesn't make a distinction between branches and tags.
A tag directory can contain more changes after having split from its branch,
unlike Git tag, which typically stays static, unless explicitly reassigned by force.
The resulting Git tag will be set to the last commit of such directory.

The program makes a new Git commit on a branch when there are changes in its mapped directory tree.
The commit message, timestamps and author/committer are taken from the SVN commit information.
