# git-time-shift

`git-time-shift` rewrites author and committer timestamps for a git-compatible commit range.

It supports two workflows:

1. **Offset mode**: shift both timestamps for every selected commit with expressions such as `+1d -10h`.
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
python3 git_time_shift.py <range> [offset ...] [--format FORMAT]
```

### Offset mode

Shift the last three commits by one day and back ten hours:

```bash
python3 git_time_shift.py HEAD~3..HEAD +1d -10h
```

Shift all commits that are in `feature` but not in `main`:

```bash
python3 git_time_shift.py main..feature +2h
```

### Editor mode

Open an editable file for the selected range:

```bash
python3 git_time_shift.py HEAD~5..HEAD
```

Each editable line looks like this:

```text
author=2026-04-27 17:05:46+00:00 | committer=2026-04-27 17:06:10+00:00 abc1234 Example commit subject
```

Change only the timestamp values. After you save and exit, the tool prints a preview and asks for confirmation.

## Format selection

The default format is `rfc-3339=seconds`.

`--format` accepts:

- GNU `date`-style custom formats starting with `+`
- selector-style values such as:
  - `rfc-3339`
  - `rfc-3339=date`
  - `rfc-3339=minutes`
  - `rfc-3339=seconds`
  - `rfc-3339=ns`
  - `iso-8601`
  - `iso-8601=hours`
  - `iso-8601=minutes`
  - `iso-8601=seconds`
  - `iso-8601=ns`

Example:

```bash
python3 git_time_shift.py HEAD~2..HEAD --format '+%Y-%m-%d %H:%M:%S %:z'
```

## Offset syntax

Offset expressions are space-separated tokens:

- `y` = years
- `mo` = months
- `w` = weeks
- `d` = days
- `h` = hours
- `m` = minutes
- `s` = seconds

Examples:

```bash
python3 git_time_shift.py HEAD~4..HEAD +1d
python3 git_time_shift.py HEAD~4..HEAD +1d -10h +30m
python3 git_time_shift.py HEAD~4..HEAD +2mo
```

## Notes

- Offset mode shifts author and committer times independently.
- Editor mode shows both timestamps and lets you edit both values explicitly.
- The tool rewrites refs with `git filter-branch -- --all` so descendants stay consistent across local branches and tags.
- `git filter-branch` leaves backup refs in `refs/original/`.
