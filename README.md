# svn-to-git: Subversion dump parsing and conversion to Git

This Python program allows you to read and analyze a dump of Subversion (SVN) repository,
and also optionally convert it to Git repository.

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

`python svn-to-git.py <input files...> [<options>]`

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

`--verbose={dump|revs|commits|merges|all|dump_all}`
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

	`--verbose=commits`
	- issue `git show --raw --parents --no-decorate --abbrev-commit` to the log for each commit made during SVN to Git conversion.

	`--verbose=merges`
	- add svn:mergeinfo processing information to the log.

	`--verbose=merges-verbose`
	- add a lot more of svn:mergeinfo processing information to the log.

	`--verbose=all`
	- same as `--verbose=dump --verbose=revs --verbose=commits --verbose=merges`

`--verify-data-hash` (or `-V`)
- Verify integrity of the SVN dump file by checking the hashes.

`--path-filter <path filter glob>`
- selects SVN paths to filter for. This option can appear multiple times. See [Path filtering](#path-filtering).

`--project <project name filter>`
- selects projects to process. This option can appear multiple times. See [Project filtering](#project-filtering).

`--target-repository <target Git repository path>`
- Specifies path to the target Git repository.
The repository should be previously initialized by a proper `git init` command.
The program will not delete existing refs, only override them as needed.

`--decorate-commit-message <tagline type>`
- tells the program to add a tagline to each commit message, depending on `<tagline type>`.
At this time, the only `<tagline type>` supported is `revision-id`,
which tells the program to add `SVN-revision: <rev>` taglines with SVN revision number to each commit.
By default, the commit messages are undecorated.

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

If a `<Project>` section relies on another project section,
for example, it merges paths from another project, specify such requirement with
`NeedsProjects="comma,separated,list"` attribute.

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

`<MapPath>` and `<UnmapPath>` definitions are processed in their order in the config file in each `<Project>`.
First `<Project>` definitions are processed, then definitions from `<Default>`,
and then default mappings described above (unless they are suppressed by `--no-default-config` command line option).

If a `path matching spec` ends with a '`/*`' wildcard, which means it matches all directories in its parent directory,
an implicit unmapping rule is created for the parent directory,
so the parent directory will never be mapped to a branch, even if there's a matching map for it.
If you still want to create a branch for the parent directory, to commit files which it could contain,
add `BlockParent="No"` attribute to `<MapPath>` section header.

The first `<MapPath>` with `<Path>` matching the beginning of the directory path will define which Git "branch" this directory belongs to.
The rest of the path will be a subdirectory in the branch worktree.
For example, an SVN directory `/branches/feature1/includes/` will be matched by `<Path>**/$Branches/*</Path>`
and the specification `<Refname>refs/heads/**/*</Refname>` will map it to ref `refs/heads/feature1` worktree directory `/includes/`.

The target refname in `<Refname>` specification is assumed to begin with `refs/` prefix.
If the `refs/` prefix is not present, it's implicitly added.

If a refname produced for a directory collides with a refname for a different directory,
the program will try to create an unique name by appending `__<number>` to it.

The program creates a special ref for each commit it makes, to map SVN revisions to Git commits.
An optional `<RevisionRef>` specification defines how the revision ref name root is formatted.
Without `<RevisionRef>` specification, an implicit mapping will make
refnames for branches (Git ref matching `refs/heads/<branch name>`) as `refs/revisions/<branch name>/r<rev number>`,
and for tag branches (Git ref matching `refs/tags/<tag name>`) as `refs/revisions/<branch name>/r<rev number>`.

To explicitly block a directory and all its subdirectories from creating a branch, use a `<UnmapPath>` specification:

```xml
	<Project>
		.....
		<UnmapPath>path matching specification</UnmapPath>
		.....
	</Project>
```

If a `path matching specification` in `<UnmapPath>` ends with a '`/*`' wildcard,
which means it matches all directories in its parent directory,
then an implicit `<UnmapPath>` rule is created for the parent directory.
If you don't want to implicitly unmap the parent directory,
add `BlockParent="No"` attribute to `<UnmapPath>`.

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

Git refname remapping
-----------------

The program allows to remap the created refnames further by using `<MapRef>` specification:

```xml
	<Project>
		<MapRef>
			<Ref>ref matching specification</Ref>
			<NewRef>ref substitution string</NewRef>
		</MapRef>
	</Project>
```

The matching and substitution rules are similar to `<MapPath>` specification.
The `ref matching specification` needs to match the full source refname.
All `<MapRef>` definitions from `<Default>` are processed *after* all sections in `<Project>`.

If `<NewRef>` is omitted, the ref will not be issued into the target Git repository.
This allows to delete the unwanted refs, such as obsolete branches, partially merged to the trunk.

Note that character substitution (specified by `<Replace>`) is done after refname remapping.

Commit message editing{#Commit-message-editing}
----------------------

You can fix commit messages if you don't like what's in the original revisions.

The message editing is done by `<EditMsg>` specifications, which can be present at the project level,
or in `<MapPath>` specifications.

```xml
	<Project>
		<EditMsg Final="yes" Revs="revisions" Max="max substitutions">
			<Match>regular expression</Match>
			<Replace>substitution</Replace>
		</EditMsg>
		<MapPath>
			...
			<EditMsg Final="yes" Revs="revisions" Max="max substitutions">
				<Match>regular expression</Match>
				<Replace>substitution</Replace>
			</EditMsg>
		</MapPath>
	</Project>
```

`<Match>` specification contains a regular expression (regex) in format supported by Python `re` module.
The match is performed in `MULTILINE` mode, where `^` character matches start of each line of the whole message,
and `$` also matches end of each line (before a newline).
Use `\A` to match the start of the message, and `\Z` to match the end.

To match a period, don't forget escape it with a backslash, as `\.`.

Keep in mind that `.` normally matches all *except* a newline. To have it match everything, including a newline,
enable DOTALL match mode by enclosing the pattern in `(?s:` `pattern` `)`.

To replace the whole message, omit the `<Match>` specification altogether.

`<Replace>` specification contains a substitution string in format expected by `re.sub()` function.
Note that backslashes don't have a special meaning in XML text,
but ampersand, quote, apostrophe, "less" and "greater" characters
needs to be replaced with special sequences: `&amp;`, `&quot;`, `&apos;`, `&lt;`, `&gt;`.

Multiple `<EditMsg>` specifications can be present.
The program first performs match and replacement for specifications in `<MapPath>` block for the current branch,
then for specifications in `<Project>` and `<Default>` blocks.

`Final="yes"` attribute in the `<EditMsg>` specification tells the program to stop processing other
such specification if this one matched the message and performed a substitution.
If `Final="yes"` attribute is not present (or the value does not represent "true"),
the program continues to match and replace the given commit message after a replacement was done.

`Revs="revisions"` specifies comma-separated numerical revision numbers or revision ranges as `start-end`,
to which this specification applies.

`Max="max substitutions"` limits number of times the substitution will be made in a single commit message.
If not present, or `"0"`, the pattern substitution will not be limited.

Note that you can produce different commit messages for commits on same revision in different branches,
by using different `<EditMsg>` specifications in separate `<MapPath>` blocks for those branches.

If the resulting non-blank message starts with two newlines,
the program will insert an added/changed/deleted/renamed files summary as the commit subject line.
If you want to add this automatically every time the original message starts from multiple
non-blank lines, use the following edit specification:

```xml
	<Project>
		<EditMsg Final="yes" Revs="revisions" Max="max substitutions">
			<Match>\A(.+\n.+$)</Match>
			<Replace>\n\n\1</Replace>
		</EditMsg>
	</Project>
```

Handling of empty commit messages
---------------------------------

Subversion allows empty commit messages. An empty message may also be produced by [commit message editing](#Commit-message-editing).

The program will generate a commit message describing all added, deleted, changed, renamed files.

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

SVN merge handling{#svn-merge-handling}
------------------

SVN is using different concepts to track merges than Git.
Since SVN doesn't have a concept of commit graph,
it maintains `svn:mergeinfo` attribute for files and directories.

The program detects `svn:mergeinfo` delta and adds the new merge information to the commit message.

The program is also using SVN file/directory copy operations to figure out when to create merge commits.
If a whole branch worktree gets merged to the current branch by a copy operation,
the program makes a merge commit for it.

By default, the program treats `svn:mergeinfo` attribute as inheritable from parent directories.
If a branch doesn't have such an attribute at its root directory,
the program will look for the attribute in one of its parent directories.
In some repositories, the attribute in a parent directory may get malformed.
In such a case, you'll want to disable its inheritance, by specifying `InheritMergeinfo="No"`
attribute in the `<MapPath>` section for the current branch or in the current project:

```xml
	<Project InheritMergeinfo="No">
		<MapPath InheritMergeinfo="No">
			<Path>path matching specification</Path>
			<Refname>ref substitution string</Refname>
		</MapPath>
	</Project>
```

The program detects single- and multiple- revision merges
and adds `Cherry-picked-from: <commit-ID>` lines to the commit message of such cherry-picks.

Single branch merges are fast-forwarded, when possible.

Merge creation from `svn:mergeinfo` delta and from applicable file copy is enabled
by the `RecreateMerges="Yes/No/branch_merge|file_merge|dir_copy|file_copy"` attribute in the `<MapPath>` section
for the current branch or the current project:

```xml
	<Project RecreateMerges="Yes">
		<MapPath RecreateMerges="No">
			<Path>path matching specification</Path>
			<Refname>ref substitution string</Refname>
		</MapPath>
	</Project>
```

The attribute string can be either `Yes`, `No`,
or comma-separated combination of `branch_merge`, `file_merge`, `dir_copy`, `file_copy`.

`branch_merge` enables merge recreation when `svn:mergeinfo` change suggests a whole branch directory has been merged.  
`file_merge` enables merge recreation when `svn:mergeinfo` change suggests
a file has been merged from a branch with similar directory structure.  
`dir_copy` enables merge recreation when a subdirectory of a source branch is copied.  
`file_copy` enables merge recreation when a single file of a source branch is copied,
and the tree structure of the source and the target branches are very similar.

`RecreateMerges="Yes"` is equivalent to `RecreateMerges="branch_merge,dir_copy"`.

If the project has `RecreateMerges="Yes"` attribute,
but you want to disable it for the given `<MapPath>` specification,
add `RecreateMerges="No"` attribute to `<MapPath>`.

A merge is only created if `svn:mergeinfo` delta covers all revisions to be merged.

You can exclude revisions which only change certain files from such merge coverage check.
These files are specified by `IgnoreUnmerged="path1;path2..."` wildcard specifications.
These paths are relative to the branch root directory.

Merge creation from a whole directory copy where the source and target paths match branch roots,
is always enabled.

Handling of deleted branches
----------------------------

In SVN, a directory used as a branch or as a tag can be deleted, terminating its history.
The directory can be later re-created again.

**svn-to-git** program handles these cases by deleting and re-starting a branch when its SVN directory is deleted and re-created.
The point where a branch got deleted is assigned a ref with `_deleted@r<rev>` suffix,
but only if that point haven't been merged into another branch.
If it's been merged, no special ref is assigned to it.

Automatic deletion of merged branches
-------------------------------------

The program can automatically delete a branch (do not write a ref for it) if it's been merged to another branch.
To enable this behavior, add `DeleteIfMerged="Yes"` attribute to `<MapPath>` section tag:

```xml
	<Project>
		<MapPath DeleteIfMerged="Yes">
			........
		</MapPath>
	</Project>
```

Note that there's no support for such attribute at `<Project>` level.

Injection of files to branch tree
---------------------------------

The program allows you to inject files, such as `.gitattributes`, `.gitignore`, `.editorconfig` to the whole history of branches.
Injection is requested by adding `<InjectFile>` directive to `<Project>` or to `<Default>`,
which can either use immediate data, or load data from a file.

`<InjectFile>` specification injects a file relative to branch worktree.

```xml
	<Project>
		<!-- Use file data -->
		<!-- Branch attribute is optional -->
		<InjectFile Path="<file path>" File="<source file path>" Branch="<branch filter globspec>" />
		<!-- Use immediate data -->
		<InjectFile Path="<file path>" Branch="<branch filter globspec>">File data
</InjectFile>
	</Project>
```

Here, the mandatory `Path` attribute specified the injected file pathname,
relative to the branch worktree root.

For example, to inject `.gitignore` to the root directory of a branch, specify `Path=".gitignore"`.

Optional `Branch` attribute filters which branches are subject to injection of this file.
The attribute value is a glob specification to match the branch SVN directory path.

`<AddFile>` adds or replaces a file at the specified SVN path and revision,
equivalent to a file being added or modified in the SVN repository at the revision:

```xml
	<Project>
		<!-- Use file data -->
		<AddFile Path="<file path in SVN repo>" File="<source file path>" Rev="<add/replace at revision>" />
		<!-- Use immediate data -->
		<AddFile Path="<file path in SVN repo>" Rev="<add/replace at revision>">File data
</AddFile>
	</Project>
```

A file injected at one revision can be overridden by another file injected at different revision.
This directive can also override a file which was previously present in a repository.

If a file gets replaced, its SVN properties are kept unchanged.

`<InjectFile>` specification can also be present inside `<MapPath>` section:

```xml
	<Project>
		<MapPath>
			<!-- Use file data -->
			<!-- Branch attribute is optional -->
			<InjectFile Path="<file path>" File="<source file path>" Branch="<branch filter globspec>" />
			<!-- Or use immediate data -->
			<InjectFile Path="<file path>" Branch="<branch filter globspec>">File data
</InjectFile>
		</MapPath>
	</Project>
```

In this case, the `<InjectFile>` directive applies only to branches mapped by this `<MapPath>` section.
`Path="<path"` attribute also specifies path relative to the branch root here.

If data is to be loaded from a file specified by `File="<source file path>"` attribute,
it's committed as is (with possible conversion defined by implicit and explicit EOL conversion rules).
Note that the source file path is relative to the directory of this XML configuration file.

If immediate data is used, keep in mind the text is used exactly as included
between opening and closing XML tags, converted to UTF-8 encoding.

If a `.gitattributes` file is injected, this file will be used by Git during conversion from SVN revisions,
for EOL conversion and optionally encoding conversion (`working-tree-encoding` attribute).

WARNING: Git may leave lone CR (carriage return) characters as is during the conversion.

`svn:gitignore` attribute handling
------------------------

In Subversion, you can assign `svn:gitignore` attribute to a directory.
The program converts these attributes to `.gitignore` files.

Performance optimizations
--------------------------

To speed up the conversion, the program employs parallel processing, where appropriate.

First of all, current implementation of Python interpreter doesn't take full advantage of multiple threads,
because it uses the infamous Global Interpreter Lock (GIL). Only one thread interprets the bytecode at any time.

Yet, some functions, such as SHA1 calculations, can release the GIL temporarily and run truly in parallel with other threads.
Also, it can spawn other processes, such as Git, which will be also running in parallel.
Some Git operations, though, may have constraints on parallel operations.

The main thread reads the dump file, reconstructs the SVN tree history, and create branches from the tree.
Necessary Git hashing operations are queued.
The program runs `git hash-object` operations by spawning up to 8 instances of Git.

Note that identical blobs introduced by different revisions are only run through `hash-object` once.
If different branches or revisions introduce same file contents, it doesn't add extra hashing overhead.
