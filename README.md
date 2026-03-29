# git-time-shift

`git-time-shift` rewrites author and committer timestamps for a git-compatible commit range or a single commit reference.

It supports two workflows:

1. **Offset mode**: shift both timestamps for every selected commit with a single expression such as `1d30m` or `-1d1h`.
2. **Editor mode**: open an editable file, let you change author and committer times commit-by-commit, preview the result, then confirm with `y/n`.

## Requirements

- `git`
- `python3`
- an editor available from `GIT_EDITOR`, `VISUAL`, `EDITOR`, or `vi`

No third-party Python packages are required.

## Warning

This tool rewrites history. Commit IDs will change for the selected commits and for their descendants. Run it on disposable branches or after creating a backup.

## Usage

```bash
python3 git_time_shift.py <range> [--offset OFFSET] [--format FORMAT]
```

You can pass a range such as `HEAD~3..HEAD` or a single commit reference such as `HEAD`, `HEAD~2`, or a commit hash. A single commit reference edits only that one commit.

### Offset mode

Shift the last three commits forward by one day and thirty minutes:

```bash
python3 git_time_shift.py HEAD~3..HEAD --offset 1d30m
```

Shift all commits that are in `feature` but not in `main` back by one day and one hour:

```bash
python3 git_time_shift.py main..feature --offset=-1d1h
```

### Editor mode

Open an editable file for the selected range:

```bash
python3 git_time_shift.py HEAD~5..HEAD
```

Open an editable file for just `HEAD`:

```bash
python3 git_time_shift.py HEAD
```

Each editable line looks like this:

```text
author=2026-04-27 17:05:46+00:00 committer=2026-04-27 17:06:10+00:00 abc1234 Example commit subject
```

If the author and committer times are identical, the editable line shows just one timestamp:

```text
2026-04-27 17:05:46+00:00 abc1234 Example commit subject
```

Change only the timestamp values. After you save and exit, the tool prints a preview and asks for confirmation.

## Format selection

The default format is `rfc-3339`.

All standard formats render whole seconds.

`--format` accepts:

- `rfc-3339`: `2024-02-03 04:05:06+00:00`
- `iso-8601`: `2024-02-03T04:05:06+00:00`
- `rfc-2822`: `Sat, 03 Feb 2024 04:05:06 +0000`
- `unix`: `1706933106`

Examples:

```bash
python3 git_time_shift.py HEAD~2..HEAD --format rfc-3339
python3 git_time_shift.py HEAD~2..HEAD --format iso-8601
python3 git_time_shift.py HEAD~2..HEAD --format rfc-2822
python3 git_time_shift.py HEAD~2..HEAD --format unix
```

## Offset syntax

Offset expressions use a single value. A leading sign applies to the whole expression, and each expression can contain multiple units:

- `y` = years
- `mo` = months
- `w` = weeks
- `d` = days
- `h` = hours
- `m` = minutes
- `s` = seconds

Examples:

```bash
python3 git_time_shift.py HEAD~4..HEAD --offset 1d
python3 git_time_shift.py HEAD~4..HEAD --offset 1d10h30m
python3 git_time_shift.py HEAD~4..HEAD --offset=-1d1h
python3 git_time_shift.py HEAD~4..HEAD --offset 2mo
```

## Notes

- Offset mode shifts author and committer times independently.
- Editor mode shows both timestamps and lets you edit both values explicitly.
- The tool rewrites refs with `git filter-branch -- --all` so descendants stay consistent across local branches and tags.
- `git filter-branch` leaves backup refs in `refs/original/`.
