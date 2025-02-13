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

	`--verbose=format`
	- log format specifications for all files subject to reformat.

	`--verbose=format-verbose`
	- also log information about files explicitly excluded from formatting.

	`--verbose=all`
	- same as `--verbose=dump --verbose=revs --verbose=commits --verbose=merges --verbose=format`

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
By default, the commit messages are undecorated.

`--decorate-commit-message revision-id`
- add `SVN-revision: <rev>` taglines with SVN revision number to each commit.

`--decorate-commit-message change-id`
- enable insertion of Gerrit `Change-Id:` taglines into commit messages.
Use this option if you intend to import the generated Git repository into Gerrit code review system.
Change ID for a commit is generated as SHA1 hash over combination of its parent commit IDs,
author name, email and timestamp, and the commit message.
Multiple runs of the program produce identical change IDs.

`--create-revision-refs`
- generate a ref (symbolic reference) for each commit, using a mapping provided by an explicit `<RevisionRef>` specification,
or a default mapping.
For commits on branches the default mapping is in form `refs/revisions/<branch name>/r<rev id>`.
For commits on tag "branches", the default mapping is in form `refs/revisions/tags/<tag name>/r<rev id>`.
Note that if a tag is set on a commit belonging to a branch, a separate revision ref is not made for it.

`--link-orphan-revs`
- this option is useful in case an SVN repository starts from a snapshot of previous repository, with pre-existing branches and tags.
See [Linking orphan revisions](#Linking-orphan-revisions).

`--add-branch-tree-prefix`
- this option enables adjusting of branch root directory prefix.
See [Tree prefix option](#Tree-prefix-option).

`--replace-svn-keywords`
- replace certain expandable SVN keywords with their values.
See [SVN keyword expansion](#SVN-keyword-expansion).

`--retab-only`
- instead of indent reformatting only re-tabulates the leading whitespaces,
as if `RetabOnly="Yes"` was specified in `<Formatting>` specifications.
This option overrides `--no-indent-reformat`.

`--no-indent-reformat`
- Disables indent reformatting, specified by `<Formatting>` specifications.
Trailing whitespace trimming is still done.

`--append-to-refs refs/<prev-ref-root>`
- This option allows to join history of the new Git repository to another repository.
See [Joining histories of separate SVN repositories](#append-to-refs) section.

`--authors-map <authors-map.json file>`
- specifies a JSON file to map SVN usernames to Git author/committer names and emails,
see [Mapping SVN usernames](#Mapping-SVN-usernames) section.

`--make-authors-map <authors-map.json file>`
- specifies filename to write a template JSON file for mapping SVN usernames to Git author/committer names and emails,
see [Mapping SVN usernames](#Mapping-SVN-usernames) section.

`--sha1-map <map filename.txt>`
- speed up processing by reusing formatting/hashing from previous runs.
The hash map file will be read (if exists) before the run, and written after the run completes.
It maps an internal hash (composed from `.gitattributes` tree hash,
file path and data hashes, and the format specification hash) into Git blob hash.

`--prune-refs <refs filter>`
Selects refs namespace to prune in the target Git repository.
See [Pruning stale refs](#Pruning-stale-refs).

`--extract-file <SVN path>,r<revision> <dest filename>`
- extract a file by SVN path and revision, into a file by `<dest filename>`.
Useful mainly for debugging.
This option can also be used when running without a target Git repository.

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
If `--create-revision-refs` is present in the command line, an implicit `<RevisionRef>` mapping
will be added, if not present.

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

If `--create-revision-refs` is present in the command line,
the program creates a special ref for each commit it makes, to map SVN revisions to Git commits.
An optional `<RevisionRef>` specification defines how the revision ref name root is formatted.
A default mapping will make
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

Some directories may not match any of the `<MapPath>` directive,
and thus remain not mapped to any branch.
The program prints list of these directories to the log file.

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

Subdirectory branch mapping
---------------------------

A subdirectory can be mapped to one branch while its parent directory (or one of its parents)
will be mapped to another branch.
This is useful when an SVN repository branching policy have been very loose,
and directories have been branched without care.
During conversion to Git, you can untangle them and make neat Git branches with nice contiguous history.

To make sure a subdirectory got mapped to its own branch,
its `<MapPath>` specification should come in `<Project>` section
before the mapping specification for its parent directory.

Such subdirectory will not be present in the Git branch history of its parent directory,
as if it was never there.

In some cases, you want to merge changes in the child directory branch to the branch of its parent directory.
To enable that, add `MergeToParent="Yes"` attribute to its `<MapPath>` specification:

```xml
	<Project>
		<MapPath MergeToParent="Yes">
			......
		</MapPath>
	</Project>
```

Commits in this branch will also make a merge commit from it to its parent branch.

You can choose to do a lazy merge,
by specifying `LazyMergeToParent="Yes"` attribute instead of `MergeToParent="Yes"`.
With lazy merge, the subdirectory branch will only trigger a merge commit to its parent branch
when another branch is created off the parent, or the parent is copied to a tag.

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
Use [`<Formatting FixEol="Yes">`](#fix-eol) attribute to convert stray CR to LF in the repository.

`svn:gitignore` attribute handling
------------------------

In Subversion, you can assign `svn:gitignore` attribute to a directory.
The program converts these attributes to `.gitignore` files.

Ignoring files from SVN tree
----------------------------

Quite often, when a repository haven't been properly setup to ignore temporary files and build artifacts,
those files get committed by mistake. During SVN to Git conversion,
the program can ignore those files and drop them from the resulting Git commits.

To ignore files, use `<IgnoreFiles>` directives.
The directives can be present in `<Default>` or `<Project>` section, or in `<MapPath>` section:

```xml
	<Project>
		<IgnoreFiles>glob pattern....</IgnoreFiles>
		......
		<MapPath>
			<IgnoreFiles>glob pattern....</IgnoreFiles>
		</MapPath>
	</Project>
```

The directive contains a semicolon-separated list of pathname patterns to ignore.
Multiple `<IgnoreFiles>` directives can be present.

If a pattern is prefixed with an exclamation mark '`!`',
it means this pattern is excluded from ignore (negative match).

First, glob patterns in the directive under `<MapPath>` section are matched against paths relative to a branch root.

Then glob patterns in the directive under `<Default>` or `<Project>` section are matched against paths in SVN repository tree.
All `<IgnoreFiles>` definitions from `<Default>` are processed *after* all sections in `<Project>`.

The program matches paths against each glob pattern in sequence, until a match is found.
If it's a negative match (the pattern is prefixed with '`!`'), the file is not ignored.

Ignored files are logged, with `IGNORED:` prefix.
If a whole directory is ignored, files under it are not printed separately.

Deleting files and directories from SVN tree
----------------------------

You can delete files and directories (make them non-present in the resulting Git repository) at the given SVN revision,
by using `<DeletePath>` directive under `<Default>` or `<Project>` section.
You can delete files present in the original SVN repo dump, and also files injected by `<AddFile Path="path">` directive.
You cannot delete files injected by `<InjectFile Path="path">` directive.

```xml
	<Project>
		<DeletePath Path="path" Rev="revision" />
	</Project>
```

`Path="path"` attribute specifies the file path in SVN repository tree.
`Rev="revision"` specifies the SVN revision number at which the file is to be deleted.

Unlike `<IgnoreFiles>` directive, `<DeletePath>` lets you delete a file at the specified revision.

Copying SVN files and directories
---------------------------------

Sometimes you need to simulate SVN copy operation,
to move a tree or a file into a directory you want it to be in your new Git repository.
Use `<CopyPath>` directive to perform a copy.
Note that this operation also creates a connection in SVN history from one place to another,
just like SVN copy operation does.

`<CopyPath>` directives can only be present in a `<Project>` section.
If it's present  under `<Default>` section, it's ignored.

```xml
	<Project>
		<CopyPath>
			<FromPath>source file/directory path</FromPath>
			<FromRev>source revision</FromRev>
			<Path>target file/directory path</Path>
			<Rev>target revision</Rev>
		</CopyPath>
	</Project>
```

Forcing a merge
---------------

Occasionally, you want to join two lines of history left disjointed in the SVN repository, by a merge commit.
Or you'd like to fix a bad rename, when an SVN branch was renamed by external copy,
instead of using SVN copy.

Use `<MergePath>` directive to create a connection from one SVN path and revision to another SVN path and revision.
Note that this operation doesn't change the files, it just links the Git commits by a parent.

`<MergePath>` directives can only be present in a `<Project>` section.
If it's present  under `<Default>` section, it's ignored.

```xml
	<Project>
		<MergePath>
			<FromPath>source branch path</FromPath>
			<FromRev>source revision</FromRev>
			<Path>target branch path</Path>
			<Rev>target revision</Rev>
		</MergePath>
	</Project>
```

Source and target branch paths must refer to SVN directories mapped to Git branches by `<MapPath>` directives.
The SVN branches must exist (created and not deleted) at the given revisions.
This directive can also refer to directories created by previous `<CopyPath>` operation.

Changing file mode mask
-----------------------

Unlike Git, SVN doesn't keep Unix file mode.
To checkout files as executables, they can be assigned `svn:executable` attribute in SVN repository.
Though, in mis-configured repositories, this attribute is often given to files not intended to be executable.

The program assigns file mode 100644 to regular files, unless they have `svn:executable` attribute,
in which case they get file mode 100755.

Symbolic links (files with `svn:special` attribute) get file mode 120000.

You can give a different file mode to regular files, or fix misconfigured `svn:executable`,
by using `<Chmod>` specification under `<Default>` or `<Project>` section.

```xml
	<Project>
		<Chmod>
			<Path>glob pattern...</Path>
			<Mode>file mode</Mode>
		</Chmod>
	</Project>
```

Here **glob pattern** is a semicolon-separated list of patterns.
Negative patterns (do not match) should be prefixed with an exclamation mark '`!`'.
**file mode** is Unix file mode consisting of three octal digits (0-7).

All `<Chmod>` definitions from `<Default>` are processed *after* all sections in `<Project>`.

If a file relative path (in a branch worktree) matches a pattern in the list, it's committed with the specified mode.

Typically, the following `<Chmod>` specifications need to be used:

```xml
	<Project>
		<Chmod>
			<Path>*.sh;*.pl;*.so;*.exe;*.dll;*.bat;*.cmd;*.EXE;*.DLL;*.BAT;*.CMD</Path>
			<Mode>755</Mode>
		</Chmod>
		<Chmod>
			<Path>*</Path>
			<Mode>644</Mode>
		</Chmod>
	</Project>
```

This forces all files with extensions `.sh`, `.pl`, `.exe`, `.dll`, `.bat`, `.cmd`, `.so` to have mode 100755,
and all other files to have mode 100644.
`.exe`, `.bat`, `.cmd` and `.dll` files here are forced to mode 100755 (executable),
because Git under Cygwin will otherwise check them out as non-executable, and then those files won't run.

Empty directory placeholder
---------------------------

Unlike SVN, Git doesn't currently allow to commit empty directories.
A common workaround for that is to commit a zero-length file into a directory you wish to preserve in a commit.

If empty directories need to be preserved (which is rarely a case),
use `<EmptyDirPlaceholder>` specification under `<Default>` or `<Project>` section:

```xml
	<Project>
		<EmptyDirPlaceholder Name="placeholder file name">placeholder text</EmptyDirPlaceholder>
		<!-- or without data: -->
		<EmptyDirPlaceholder Name="placeholder file name" />
	</Project>
```

For example, `<EmptyDirPlaceholder Name=".gitignore" />` will place an empty `.gitignore` file
to each empty directory.

Mapping SVN usernames{#Mapping-SVN-usernames}
---------------------

SVN commits only store short usernames for commit authors. Git stores full names and emails.
To make pretty commits, the program uses a map file in JSON format.
It consists of multiple sections which map SVN usernames to Name and Email attributes:

```json
{
	"<SVN username>": {
		"Name": "<Git name>",
		"Email": "<email>"
	},
	....
}
```

For example:

```json
{
	"dvader": {
		"Name": "Darth Vader",
		"Email": "dvader@deathstar.example.com"
	},
	"luke.skywalker": {
		"Name": "Luke Skywalker",
		"Email": "lskywalker@tatooine.example.com"
	}
}
```

To tell the program to use an authors map JSON file, specify `--authors-map=<filename.json>` command line option.

If an SVN username is not mapped, the program will make an email as `<SVN username>@localhost`,
same as Git does when `user.email` setting is not configured.

To make an initial author map file, specify `--make-authors-map=<filename.json>` command line option.
Note that the file will only contain usernames encountered while making Git commits on directories mapped to Git branches.
Then edit the produced file and use it as input for `--authors-map` option.

Linking orphan revisions{#Linking-orphan-revisions}
-----------------------

If you had a long running SVN repository, and had to migrate its snapshot,
you end up with branches and tags directories without any history.

`--link-orphan-revs` option makes the program to link newly added parent-less branches
and tags directories together into a chain of commits,
so you will be able to inspect diffs of one from another.
The linking is only done if their worktrees are similar enough.

To control orphan linking per branch map, add `LinkOrphan="Yes/No"` attribute to `<MapPath>` specifications.

NOTE: `<MergePath>` feature may be more appropriate in such cases.

Tree prefix option{#Tree-prefix-option}
------------------
When a new branch is created by SVN copy of a subdirectory an existing branch,
its first Git commit will contain deletions and renames (moves)
from a subdirectory to the root of the new branch worktree.

If `--add-branch-tree-prefix` option was given in the command line,
the program puts the new branch files into same subdirectory as they were in the parent branch.

You can also enable/disable this option per path map specification:

```xml
	<Project>
		<MapPath AddTreePrefix="Yes">
		.....
		</MapPath>
	</Project>
```

Joining histories of separate SVN repositories{#append-to-refs}
----------------------------------------------
Sometimes projects get moved from one SVN repository to another, starting just from a directory snapshot.
You can run the program on the previous repo, converting it to Git,
and then join the second SVN repository with this Git repo,
making the new Git repository with full history of your project.

Use `--append-to-refs` command line option allows you to join histories.

Suppose, you ran the program with `svn_repo1.dump`, producing Git repository `git_repo1/`.
You have the dump file of the second SVN repository `svn_repo2.dump`,
and initialized `git_repo2/` for the conversion result.

Add a "remote" (alias) to `git_repo2/` for fetching from `git_repo1/`:

```
git remote add --no-tags repo1 ../git_repo1
```

The second repository needs the following configuration for the `repo1` remote
(use `git config --edit` command to manually edit it):

```
[remote "repo1"]
	url = ../repo1/
	fetch = +refs/heads/*:refs/repo1/heads/*
	fetch = +refs/tags/*:refs/repo1/tags/*
	fetch = +refs/revisions/*:refs/repo1/revisions/repo1/*
	prune = true
	tagOpt = --no-tags
```

When a program is starting a Git branch or a tag,
it looks up the new branch/tag name in the refs present in the ref namespace
specified by `--append-to-refs` option.
For the name lookup, the ref name is mapped from `refs/` to `refs/<namespace>/`.
If same name is found there, the new branch/tag is connected, by assigning a commit parent,
to the commit at the top of the found ref.
Thus, the new branch/tag becomes a continuation of the branch from the previous repository.

Newly created branches (`refs/heads/<branchname>`) will attach to the existing
branches under `refs/<namespace>/heads/<branchname>`.
Newly created tags (`refs/tags/<tagname>`) will attach to the existing
tags under `refs/<namespace>/tags/<tagname>`.

When the program run completes, all un-linked refs from `refs/<namespace>/`
are transferred to `refs/` namespace.

If the newly created branch has "add tree prefix" option enabled,
either by `--add-branch-tree-prefix` command line option,
or by `AddTreePrefix="Yes"` attribute in `<MapPath>` specification,
the program will detect the required prefix by matching the previous and new trees.

Pruning stale refs{#Pruning-stale-refs}
-----------------

When you run the program multiple times to fine-tune the path mapping specifications,
the changes in the mapping can cause some refs (branch or tag names, also revision refs)
not to be created anymore, because of name change, for instance.
You'd want the old ref names to disappear.
By default, the program doesn't clean the Git refs before its run.

`--prune-refs <refs namespace>` command line option tells the program to clean stale ref names from the selected namespace(s).

If `<refs namespace>` specification doesn't start with `heads`, `refs/heads`, `tags`, `refs/tags`, `revisions`, `refs/revisions`, it's assumed to cover `refs/heads/<refs namespace>/`, `refs/tags/<refs namespace>`, `refs/revisions/<refs namespace>`.

If `<refs namespace>` is omitted in `--prune-refs` option, the program uses `Refs="refs namespaces"` attribute from `<Project>` sections:

```xml
	<Project Refs="refs namespaces">
		.....
	</Project>
```

The attribute value can contain multiple namespace specifications, separated with semicolon '`;`'.
If projects are filtered by  `--project` command line options,
only `Refs` attributes from active `<Project>` sections are used for ref pruning.
If `Refs` attribute is not present in `<Project>` section, it's assumed equal to '`*`',
which means it covers `refs/heads/*`, `refs/tags/*`, `refs/revisions/*`.

SVN keyword expansion{#SVN-keyword-expansion}
---------------------

SVN can expands certain keywords when files are checked out to a worktree.
Git doesn't support expansion of those keywords on checkout.

`--replace-svn-keywords` command line option tells the program to replace those keywords with their expected values during conversion to Git commits.

SVN keywords are referred in the text files as '`$Keyword: $`'.
The following keywords are supported:

`$Date` or `$LastChangedDate`
- timestamp of the last commit changing the file. Note that SVN file copy (with no change) operation doesn't constitute a change.
Thus a trivial merge doesn't update it, which is what you'd expect of Git merge.

`$Revision` or `$Rev` or `$LastChangeRevision`
- SVN revision of last change of the file.

`$Author` or `$LastChangedBy`
- Author of the last change of the file.

`$Id`
- combination of filename, last change revision, short date and time of last change revision, author.

`$Header`
- combination of full path, last change revision, short date and time of last change revision, author.

These keywords are only expanded for files which have them enabled in `svn:keywords` file attribute.
Note that for expansion enablement, different short forms of a keyword are considered equivalent.
Thus, if `svn:keywords` contains `Rev` word, `Revision` keyword form is also considered enabled for expansion.

Combining revisions into one commit
----------------------------------

Sometimes you miss some changes in a commit, and have to make a second commit immediately to correct that.
With Git, you typically amend the previous commit. With other revision control systems, you may not have this option.

When you convert such history to Git, you can combine these two or more revisions into a single commit.

Use `<SkipCommit>` specification in the configuration file:

```xml
	<Project>
		<SkipCommit Revs="revisions">
			<Message>replacement message</Message>
		</SkipCommit>
		<MapPath>
			...
			<SkipCommit Revs="revisions">
			<Message>replacement message</Message>
			</SkipCommit>
		</MapPath>
	</Project>
```

`Revs="revisions"` specifies comma-separated numerical revision numbers or revision ranges as `start-end`,
to which this specification applies.

`Revs` attribute must be present with non-empty list of revisions.

The message of a skipped revision is carried over to the next revision to be combined as the final commit message.
It goes to the front of the combined message.

Optional `<Message>` specification contains a new message for the revision being skipped.
If you want to drop its message altogether in favor of the next revision's message, make it empty:

```xml
	<Project>
		<SkipCommit Revs="revisions">
			<Message></Message>
		</SkipCommit>
		<MapPath>
			...
			<SkipCommit Revs="revisions">
			<Message></Message>
			</SkipCommit>
		</MapPath>
	</Project>
```

Multiple `<SkipCommit>` specifications can be present.
The program first considers specifications in `<MapPath>` block for the current branch,
then specifications in `<Project>` and `<Default>` blocks.
This distinction only matters if you want to apply different replacement messages to commits made on different branches,
or only skip commits on some branches made from the given revision (one revision can cover multiple branch directories).

A `<SkipCommit>` specification is ignored if this revision is producing a merge commit,
or it's a base revision for another branch.

Reformatting indents in files
-------------------------------

**format_files.py** script allows to reformat indents in an existing C file and/or
convert spaces to tabs and vice versa.

The script can be invoked as:

```
python format_files.py <input file>... [-O <output file>] [options]
```
If `-O <output file>` option is not present, the data is written to standard output.

The following options are supported:

`--output <filename>` (or `-O <filename>`)
- specifies output file name. If the option is not present, the data is written to standard output.

`--style tabs|spaces`
- specifies use of tabs or spaces for indentation.

`--tab-size <tab size>`
- specifies number of character positions per tab character, 1 to 16.

`--indent-size <indent size>`
- specifies number of positions per one indentation level.

`--current-dir <current directory>` (or `-C <current directory>`)
- base directory for glob specifications.

`--trim-whitespace`
- trim trailing whitespace.

`--trim-trailing-backslash`
- removes unnecessary trailing backslashes in C files,
which were used to split a long line into shorter continuing lines.
This applies only to regular statements, not to preprocessor lines split by backslashes.
With `--trim-whitespace`, this will also drop extra blank lines added by trailing backslashes,
including extra trailing whitespace in a split preprocessor line.

`--fix-eols`
- fix lone CR characters - replace them with LF.

`--retab-only`
- Do not analyze the input files as C/C++, only re-tab their indents.

`--indent-case`
- add one more level of indentation to `case` lines in `switch` blocks.
Generally accepted formatting style puts `case` lines at the same level as opening `switch` line.
Use this option if you want your code formatted with `case` lines at one more indentation level.

`--no-indent-continuation`
- do not re-indent C statement continuation lines.
Note that this doesn't affect lines broken up by backslashes;
such continuation is never reformatted.

`--continuation=<option>`
- option for reformatting indents in C statement continuation lines.
The option can be one of the following:

	`none`
	- same as `--no-indent-continuation`

	`extend`
	- for C statement continuation lines, apply simple formatting rules,
but don't allow to shrink existing indents of those lines.

	`smart`
	- for C statement continuation lines, use the opening parenthesis position
and assignment operator position as a base indent.

By default, the continuation lines are indented according to the expression nesting level.

`--format-comments all|none|slashslash/oneline/multiline`
- reformat indents for comment lines.
`slashslash` enables re-indentation of **//** comments.  
`oneline` enables re-indentation of **/\* \*/** one line comments.  
`multiline` enables re-indentation of **/\* \*/** multiline comments.
`none` disabled re-indentation of all comments.

`--format-comments` with no arguments is same as `--format-comments=slashslash,oneline`.
The default option, if `--format-comments` is not supplied, is `all`.

Note that even if comment formatting of certain or all styles is not enabled,
its indent is still normalized to tabs or spaces, and its offset is adjusted to the change of the surrounding code offset.

`--file-list <file list>`
- Reformat files by a file list, one filename per line.
If `<file list>` is `-`, the list is read from the standard input.

With the file list, `--output` option can specify the directory to write the reformatted files.
By default, the files are reformatted in place.

Reformatting indents in files in SVN repository
-------------------------------

A legacy SVN codebase before advent of `.editorconfig` and other style enforcement tools could become quite disheveled.
When you convert it to Git, you can prettify your C files for uniform formatting.
You can also inject `.editorconfig` files to all the resulting branches by using an `<InjectFile>` directive.

File formatting is controlled by `<Formatting>` sections in `<Project>`, `<Default>`,
and `<MapPath>` specifications.
`<MapPath>` specifications are processed first, then sections in `<Project>`,
then definitions from `<Default>`.

A `<Formatting>` section has the following format:

```xml
	<Project>
		<Formatting IndentStyle="tabs|spaces"
			Indent="indent size"
			TrimWhitespace="Yes|No"
			TrimBackslash="Yes|No"
			TabSize="tab size"
			RetabOnly="Yes|No"
			IndentCase="Yes"
			ReindentContinuation="No|Yes|Extend|Smart"
			FormatComments="No|Yes|all/oneline,slashslash,multiline"
			FixEOL="Yes"
			FixLastEOL="Yes">
			<Path>path filter</Path>
			<NoReindent>pattern</NoReindent>
		</Formatting>
	</Project>
```

Here, `IndentStyle` value can be **tabs** or **spaces**.
If it's omitted, then the file indents are not reformatted.

Attribute `TrimWhitespace="Yes"` will enable trimming of trailing whitespaces.
If `IndentStyle` is set to **tabs** of **spaces**, `TrimWhitespace` is enabled by default.
To disable it, set it to **No** explicitly: `TrimWhitespace="No"`.
If `IndentStyle` is omitted, amd `TrimWhitespace` set to **Yes**,
only the trailing whitespaces will be trimmed from the file.

Attribute `TrimBackslash="Yes"` enables trimming of unnecessary trailing backslashes,
which were used to split a long line into shorter continuing lines.
This applies only to regular statements, not to preprocessor lines split by backslashes.
With `TrimWhitespace="Yes"`, this will also drop extra blank lines added by trailing backslashes,
except for extra trailing whitespace in a split preprocessor line.

`Indent` attribute sets an indent size per nesting level. Its default value is **4**.
`TabSize` attribute sets a size per tab in the original and the reformatted file.
Its default value is same as `Indent`.

`RetabOnly` attribute tells the program not to analyze C/C++ syntax
to figure out the right indentation levels,
only convert the existing spaces to tabs, or the other way around.
You can use it to re-tab your `.pl` and `.py` files.

`IndentCase="Yes"` attribute enables additional indentation of `case` lines in `switch` blocks.
Most often used formatting style puts `case` lines at the same level as opening `switch` line.
If you want your code formatted with `case` lines at one more indentation level,
specify `IndentCase="Yes"` attribute in `<Formatting>`.

Optional `ReindentContinuation="No|Yes|Extend|Smart"` attribute
controls formatting of statement continuation lines.
By default, the continuation lines are reformatted with indent levels adjusted,
depending on the parentheses and other nesting.

`ReindentContinuation="No"` leaves the continuation lines indent as is,
only converting the indent characters to tabs or spaces.
This doesn't apply to code lines split by backslash characters '`\`'.
Their continuation lines are always left as is.

`ReindentContinuation="extend"` controls formatting of C/C++ statements spanning several lines.
It applies apply simple formatting rules, but doesn't allow to shrink existing indents of those lines.

`ReindentContinuation="smart"` controls continuation of function call and function header lines,
and also of parenthesized expressions in other contexts.
If a function argument list is split to multiple lines,
it will continue from the position of its opening parenthesis.

`FormatComments="No/Yes/all/oneline,slashslash,multiline"` attribute controls reindentation of various styles of comments.  

`FormatComments="oneline"` enables re-indentation of **/\* \*/** one line comments.  
`FormatComments="multiline"` enables re-indentation of **/\* \*/** multiline comments.  
`FormatComments="slashslash"` enables re-indentation of **//** comments.  
The default option is **all**.  
Multiple options can be present in the attribute, separated by commas.

`FormatComments="all"` or `FormatComments="yes"` enables all of the above.

Note that even if comment formatting of certain or all styles is not enabled,
its indent is still normalized to tabs or spaces.

{#fix-eol}
`FixEOL="Yes"` attribute enables fixing of stray CR (carriage return) characters.
For files with explicit or implicit (auto) text attribute,
Git will only convert CR+LF character pairs to LF on checkin.
Lone CR characters will be left as is, but may be converted to CR+LF on checkout.
This may cause unexpected diffs in the worktrees upon checkout.
This attribute enables conversion of such lone CR characters to LF.

If a lone CR character is encountered, and EOL fixing is not enabled for this file,
the program will issue a warning into the log file:

```
WARNING: file <filename>: Line <line number> contains a stray CR character
```

Note that the warning is only issued if a file goes through formatting/prettifying by `<Formatting>` specification.

`FixLastEOL="Yes"` attribute forces appending of LF (line feed) character to the last line of a file,
if it ends without one.

If a file ends without end of line character, and EOL fixing is not enabled for this file,
the program will issue a warning into the log file:

```
WARNING: file <filename>: File ends without EOL character
```

Note that the warning is only issued if a file goes through formatting/prettifying by `<Formatting>` specification.

`<Path>` section contains filename match specifications, separated with semicolons '`;`'.
If a specification is prefixed with an exclamation mark '`!`',
it excludes matching filenames from this `<Formatting>` specification,
but it can be matched by `<Formatting>` specifications that follow it.

Optional `<NoReindent>` specifications contain a line match pattern (as a regular expression) to
skip re-indenting. A pattern matches from the beginning of the line, including whitespaces.
This specification is useful to skip reformatting of macro tables, for example MFC message maps.

Note that a `<NoReindent>` pattern is compiled as a byte-encoded (UTF-8) string.
Thus, if you want to apply a modifier (`+`, `*`, etc) to a single extended character (which can be encoded as multiple bytes),
the character needs to be enclosed in parentheses, for example: `(Ж)+`.

If `IndentStyle`, `RetabOnly`, `FixEOL`, `FixLastEOL`, and `TrimWhitespace` are all omitted or `false`,
this `<Formatting>` specification explicitly blocks the matching files from any reformatting/processing.

To debug indent reformat styles - see if they match well with original intended styles -
specify `--no-indent-reformat` command line option,
and fetch the result into a separate repository under `unformatted` remote name.
Then run the program without `--no-indent-reformat` option,
and now fetch it into that second repo under `formatted` remote name.
Now you can inspect diffs between `formatted` and `unformatted` branches,
to see if everything is to your taste.

Performance optimizations
--------------------------

To speed up the conversion, the program employs parallel processing, where appropriate.

First of all, current implementation of Python interpreter doesn't take full advantage of multiple threads,
because it uses the infamous Global Interpreter Lock (GIL). Only one thread interprets the bytecode at any time.

Yet, some functions, such as SHA1 calculations, can release the GIL temporarily and run truly in parallel with other threads.
Also, it can spawn other processes, such as Git, which will be also running in parallel.
Some Git operations, though, may have constraints on parallel operations.

The main thread reads the dump file, reconstructs the SVN tree history, and create branches from the tree.
Necessary Git hashing operations along with reformatting of C files are queued.
The program runs `git hash-object` operations by spawning up to 8 instances of Git.

Note that identical blobs introduced by different revisions are only run through `hash-object` once.
If different branches or revisions introduce same file contents, it doesn't add extra hashing overhead.

Rather that wait for all blob hashing to complete for each revision's commit,
the program continues processing revisions from the source SVN dump file and queuing the hashing operations.

After all blobs needed for a commit has been hashed, and all its parent commits are also done,
the program spawns a workitem in a separate thread to do `git update-index` operation to stage the new tree.
Multiple `update-index` operations for different branches can run in parallel.
For a given branch, these operations can only run in sequence.

After an `update-index` operation completes, the program spawns `git write-tree` workitem operation, to get the new tree ID.
A `write-tree` operation also writes any new tree objects necessary.
If multiple `write-tree` were to run in parallel,
it might need to write same tree objects at the same time,
causing a failure (yes, it happened).
For this reason, all `write-tree` operations are serialized; only one runs at any time.

After a `write-tree` workitem produces a tree ID for a new commit,
the program spawns `git commit-tree` workitem operation to create a new commit.
This operation takes the parent commit's ID (or multiple, for a merge commit), the tree ID,
the commit message, author and timestamps, writes a commit object and returns the new commit ID.
Even though for a given branch the `commit-tree` operations have to run sequentially,
multiple such operations can run in parallel for different branches.

After the commit is done, `git log` operation (if requested by `--verbose=commits` option)
is also spawned as a parallel workitem.

Note that for the given branch blob hashing, staging, and tree writing operation can run in parallel with `commit-tree` sequence.
The program makes sure all these operations are ordered and only start when the necessary previous operations complete.

The commits for different branches can complete out of order with their SVN revision numbers.
To make sure the log file looks as if the whole process has been sequential,
log file write is serialized.
The log is temporarily saved in memory until all previous log is written.

The SVN dump file reading by the main thread normally completes before all commits are done.
The program waits for all commits on all branches to be done,
and then writes/updates refs for all branches and tags, thus concluding its run.
