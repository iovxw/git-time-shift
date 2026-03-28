#!/usr/bin/env python3

from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime as format_rfc2822_datetime, parsedate_to_datetime
from pathlib import Path


LOCAL_TZ = datetime.now().astimezone().tzinfo or timezone.utc
ARROW = "→"
SUPPORTED_DATE_FORMATS = ("rfc-3339", "iso-8601", "rfc-2822", "unix")


class ToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommitRecord:
    full_hash: str
    short_hash: str
    subject: str
    author_dt: datetime
    committer_dt: datetime


@dataclass(frozen=True)
class DateFormatSpec:
    raw: str
    base: str


def run_command(
    args: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            env=env,
            check=check,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip()
        stdout = exc.stdout.strip()
        message = stderr or stdout or "command failed"
        raise ToolError(f"{' '.join(args)}: {message}") from exc


def git(args: list[str], *, cwd: str | None = None, env: dict[str, str] | None = None) -> str:
    return run_command(["git", *args], cwd=cwd, env=env).stdout


def get_repo_root() -> str:
    return git(["rev-parse", "--show-toplevel"]).strip()


def ensure_clean_worktree(repo_root: str) -> None:
    status = git(["status", "--porcelain"], cwd=repo_root).strip()
    if status:
        raise ToolError("repository has uncommitted changes; commit or stash them before rewriting history")


def normalize_commit_selection(repo_root: str, range_expr: str) -> str:
    expr = range_expr.strip()
    if not expr:
        return range_expr

    if ".." in expr or expr.endswith(("^!", "^@", "^-")):
        return expr

    try:
        commit_hash = git(["rev-parse", "--verify", f"{expr}^{{commit}}"], cwd=repo_root).strip()
    except ToolError:
        return expr
    if not commit_hash:
        return expr
    return f"{commit_hash}^!"


def get_commits(repo_root: str, range_expr: str) -> list[CommitRecord]:
    selection = normalize_commit_selection(repo_root, range_expr)
    output = git(
        [
            "log",
            "--reverse",
            "--topo-order",
            "--format=%H%x00%h%x00%aI%x00%cI%x00%s",
            selection,
        ],
        cwd=repo_root,
    )
    commits: list[CommitRecord] = []
    for line in output.splitlines():
        if not line:
            continue
        full_hash, short_hash, author_text, committer_text, subject = line.split("\x00", 4)
        commits.append(
            CommitRecord(
                full_hash=full_hash,
                short_hash=short_hash,
                subject=subject,
                author_dt=parse_git_iso(author_text),
                committer_dt=parse_git_iso(committer_text),
            )
        )
    if not commits:
        raise ToolError(f"range selected no commits: {range_expr}")
    return commits


def parse_git_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def normalize_date_format(raw_spec: str | None) -> DateFormatSpec:
    if not raw_spec:
        return DateFormatSpec(raw="rfc-3339", base="rfc-3339")

    spec = raw_spec.strip()
    if not spec:
        return DateFormatSpec(raw="rfc-3339", base="rfc-3339")

    if spec.startswith("--"):
        spec = spec[2:]

    normalized = spec.lower()
    normalized = normalized.replace("rfc3339", "rfc-3339").replace("iso8601", "iso-8601").replace("rfc2822", "rfc-2822")

    if normalized in SUPPORTED_DATE_FORMATS:
        return DateFormatSpec(raw=normalized, base=normalized)

    raise ToolError(
        "unsupported --format value; choose one of: rfc-3339, iso-8601, rfc-2822, unix"
    )


def format_offset(dt: datetime, *, include_seconds: bool = False) -> str:
    offset = dt.utcoffset()
    if offset is None:
        raise ToolError("datetime must be timezone-aware")
    total_seconds = int(offset.total_seconds())
    sign = "+" if total_seconds >= 0 else "-"
    total_seconds = abs(total_seconds)
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    if include_seconds:
        return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{sign}{hours:02d}:{minutes:02d}"


def format_standard_datetime(dt: datetime, spec: DateFormatSpec) -> str:
    if spec.base == "rfc-3339":
        return f"{dt.strftime('%Y-%m-%d %H:%M:%S')}{format_offset(dt)}"
    if spec.base == "iso-8601":
        return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}{format_offset(dt)}"
    if spec.base == "rfc-2822":
        return format_rfc2822_datetime(dt)
    if spec.base == "unix":
        return str(int(dt.timestamp()))
    raise ToolError(f"unsupported format: {spec.base}")


def format_datetime(dt: datetime, spec: DateFormatSpec) -> str:
    return format_standard_datetime(dt, spec)


def parse_standard_datetime(text: str, spec: DateFormatSpec) -> datetime:
    value = text.strip()
    if spec.base in {"rfc-3339", "iso-8601"}:
        if value.endswith("Z"):
            value = f"{value[:-1]}+00:00"
        if spec.base == "rfc-3339" and " " in value and "T" not in value:
            value = value.replace(" ", "T", 1)
        try:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
        except ValueError as exc:
            raise ToolError(f"invalid {spec.base} timestamp: {text}") from exc

    if spec.base == "rfc-2822":
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError) as exc:
            raise ToolError(f"invalid rfc-2822 timestamp: {text}") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=LOCAL_TZ)
        return parsed

    if spec.base == "unix":
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        except ValueError as exc:
            raise ToolError(f"invalid unix timestamp: {text}") from exc

    raise ToolError(f"unsupported format: {spec.base}")


def parse_datetime_value(text: str, spec: DateFormatSpec) -> datetime:
    return parse_standard_datetime(text, spec)


def month_shift(dt: datetime, months: int) -> datetime:
    month_index = (dt.month - 1) + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def apply_offset_token(dt: datetime, sign: int, amount: int, unit: str) -> datetime:
    if unit == "y":
        return month_shift(dt, sign * amount * 12)
    if unit == "mo":
        return month_shift(dt, sign * amount)
    if unit == "w":
        return dt + timedelta(weeks=sign * amount)
    if unit == "d":
        return dt + timedelta(days=sign * amount)
    if unit == "h":
        return dt + timedelta(hours=sign * amount)
    if unit == "m":
        return dt + timedelta(minutes=sign * amount)
    if unit == "s":
        return dt + timedelta(seconds=sign * amount)
    raise ToolError(f"unsupported offset unit: {unit}")


def parse_offset_expression(expression: str | None) -> list[tuple[int, int, str]]:
    if expression is None:
        return []

    expression = expression.strip()
    if not expression:
        return []

    sign = 1
    if expression[0] in "+-":
        sign = 1 if expression[0] == "+" else -1
        expression = expression[1:].strip()
    if not expression:
        raise ToolError(
            "invalid offset; use a single expression like 1d, -1d1h, or 2mo30m"
        )

    tokens: list[tuple[int, int, str]] = []
    index = 0
    pattern = re.compile(r"(\d+)\s*(y|mo|w|d|h|m|s)")
    while index < len(expression):
        while index < len(expression) and expression[index].isspace():
            index += 1
        match = pattern.match(expression, index)
        if not match:
            raise ToolError(
                "invalid offset; use a single expression like 1d, -1d1h, or 2mo30m"
            )
        amount = int(match.group(1))
        unit = match.group(2)
        tokens.append((sign, amount, unit))
        index = match.end()

    return tokens


def apply_offset(dt: datetime, offset_tokens: list[tuple[int, int, str]]) -> datetime:
    result = dt
    for sign, amount, unit in offset_tokens:
        result = apply_offset_token(result, sign, amount, unit)
    return result


def determine_editor(repo_root: str) -> str:
    try:
        editor = git(["var", "GIT_EDITOR"], cwd=repo_root).strip()
        if editor:
            return editor
    except ToolError:
        pass
    for key in ("GIT_EDITOR", "VISUAL", "EDITOR"):
        value = os.environ.get(key)
        if value:
            return value
    return "vi"


def open_editor(repo_root: str, file_path: str) -> None:
    editor = determine_editor(repo_root)
    command = f"{editor} {shlex.quote(file_path)}"
    try:
        subprocess.run(command, cwd=repo_root, shell=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise ToolError(f"editor exited with status {exc.returncode}") from exc


def format_author_committer_pair(author_text: str, committer_text: str) -> str:
    return f"author={author_text} committer={committer_text}"


def build_editor_buffer(commits: list[CommitRecord], spec: DateFormatSpec) -> str:
    lines = [
        "# Edit author and committer times for each commit below.",
        "# Change only the timestamp values after author=/committer=.",
        "# Lines beginning with # are ignored.",
        f"# Format: {spec.raw}",
    ]
    for commit in commits:
        author_text = format_datetime(commit.author_dt, spec)
        committer_text = format_datetime(commit.committer_dt, spec)
        lines.append(
            f"{format_author_committer_pair(author_text, committer_text)} {commit.short_hash} {commit.subject}"
        )
    lines.append("")
    return "\n".join(lines)


def parse_editor_buffer(
    content: str,
    commits: list[CommitRecord],
    spec: DateFormatSpec,
) -> dict[str, tuple[datetime, datetime]]:
    by_short_hash = {commit.short_hash: commit for commit in commits}
    updated: dict[str, tuple[datetime, datetime]] = {}

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        commit = None
        prefix = ""
        for candidate in commits:
            suffix = f" {candidate.short_hash} {candidate.subject}"
            if line.endswith(suffix):
                commit = candidate
                prefix = line[: -len(suffix)]
                break

        if commit is None:
            for candidate in commits:
                subject_suffix = f" {candidate.subject}"
                if line.endswith(subject_suffix):
                    maybe_short = line[: -len(subject_suffix)].rsplit(" ", 1)[-1]
                    if maybe_short not in by_short_hash:
                        raise ToolError(f"unknown short hash in edited file: {maybe_short}")
            for candidate in commits:
                if f" {candidate.short_hash} " in line:
                    raise ToolError(
                        f"commit subject changed for {candidate.short_hash}; only edit the timestamps"
                    )
            raise ToolError(f"could not parse edited line: {raw_line}")

        author_prefix = "author="
        committer_marker = " committer="
        if not prefix.startswith(author_prefix) or committer_marker not in prefix:
            raise ToolError(f"could not parse edited line: {raw_line}")

        author_text, committer_text = prefix[len(author_prefix):].split(committer_marker, 1)
        short_hash = commit.short_hash
        if short_hash in updated:
            raise ToolError(f"duplicate short hash in edited file: {short_hash}")
        author_dt = parse_datetime_value(author_text, spec)
        committer_dt = parse_datetime_value(committer_text, spec)
        updated[short_hash] = (author_dt, committer_dt)

    missing = [commit.short_hash for commit in commits if commit.short_hash not in updated]
    if missing:
        raise ToolError(f"edited file is missing commits: {', '.join(missing)}")

    return {
        commit.full_hash: updated[commit.short_hash]
        for commit in commits
    }


def collect_edited_dates(
    repo_root: str,
    commits: list[CommitRecord],
    spec: DateFormatSpec,
) -> dict[str, tuple[datetime, datetime]]:
    with tempfile.NamedTemporaryFile(
        mode="w+",
        prefix="git-time-shift-",
        suffix=".txt",
        encoding="utf-8",
        delete=False,
    ) as handle:
        handle.write(build_editor_buffer(commits, spec))
        temp_path = handle.name

    try:
        open_editor(repo_root, temp_path)
        edited_content = Path(temp_path).read_text(encoding="utf-8")
        return parse_editor_buffer(edited_content, commits, spec)
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


def build_offset_dates(
    commits: list[CommitRecord],
    offset_tokens: list[tuple[int, int, str]],
) -> dict[str, tuple[datetime, datetime]]:
    return {
        commit.full_hash: (
            apply_offset(commit.author_dt, offset_tokens),
            apply_offset(commit.committer_dt, offset_tokens),
        )
        for commit in commits
    }


def build_preview_lines(
    commits: list[CommitRecord],
    updated_dates: dict[str, tuple[datetime, datetime]],
    spec: DateFormatSpec,
) -> tuple[list[str], dict[str, dict[str, str]]]:
    preview_lines: list[str] = []
    mapping: dict[str, dict[str, str]] = {}

    for commit in commits:
        new_author, new_committer = updated_dates[commit.full_hash]
        changed = new_author != commit.author_dt or new_committer != commit.committer_dt
        if not changed:
            continue
        old_text = format_author_committer_pair(
            format_datetime(commit.author_dt, spec),
            format_datetime(commit.committer_dt, spec),
        )
        new_text = format_author_committer_pair(
            format_datetime(new_author, spec),
            format_datetime(new_committer, spec),
        )
        preview_lines.append(f"{old_text} {ARROW} {new_text} {commit.short_hash} {commit.subject}")
        mapping[commit.full_hash] = {
            "author": new_author.isoformat(),
            "committer": new_committer.isoformat(),
        }

    return preview_lines, mapping


def confirm(prompt: str) -> bool:
    answer = input(prompt).strip().lower()
    return answer in {"y", "yes"}


def write_mapping_file(mapping: dict[str, dict[str, str]]) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w",
        prefix="git-time-shift-map-",
        suffix=".json",
        encoding="utf-8",
        delete=False,
    ) as handle:
        json.dump(mapping, handle, ensure_ascii=True, indent=2, sort_keys=True)
        handle.write("\n")
        return handle.name


def run_filter_branch(repo_root: str, mapping: dict[str, dict[str, str]]) -> None:
    mapping_path = write_mapping_file(mapping)
    env = os.environ.copy()
    env["FILTER_BRANCH_SQUELCH_WARNING"] = "1"
    env["GIT_TIME_SHIFT_MAP"] = mapping_path
    env["GIT_TIME_SHIFT_SCRIPT"] = str(Path(__file__).resolve())
    env["GIT_TIME_SHIFT_PYTHON"] = sys.executable

    env_filter = 'eval "$("$GIT_TIME_SHIFT_PYTHON" "$GIT_TIME_SHIFT_SCRIPT" --internal-env-filter "$GIT_TIME_SHIFT_MAP")"'

    try:
        run_command(
            [
                "git",
                "filter-branch",
                "-f",
                "--tag-name-filter",
                "cat",
                "--env-filter",
                env_filter,
                "--",
                "--all",
            ],
            cwd=repo_root,
            env=env,
        )
    finally:
        try:
            os.unlink(mapping_path)
        except FileNotFoundError:
            pass


def internal_env_filter(mapping_path: str) -> int:
    commit_hash = os.environ.get("GIT_COMMIT")
    if not commit_hash:
        raise ToolError("GIT_COMMIT is not set")
    data = json.loads(Path(mapping_path).read_text(encoding="utf-8"))
    entry = data.get(commit_hash)
    if not entry:
        return 0
    print(f"GIT_AUTHOR_DATE={shlex.quote(entry['author'])}")
    print("export GIT_AUTHOR_DATE")
    print(f"GIT_COMMITTER_DATE={shlex.quote(entry['committer'])}")
    print("export GIT_COMMITTER_DATE")
    return 0


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rewrite author and committer timestamps for a git commit range."
    )
    parser.add_argument("range_expr", help="git-compatible revision range (for example: HEAD~3..HEAD)")
    parser.add_argument(
        "--offset",
        dest="offset_expr",
        help="optional offset expression, for example: -1d1h or 2mo30m",
    )
    parser.add_argument(
        "--format",
        dest="date_format",
        default="rfc-3339",
        help=(
            "display/edit format. Choose one of: "
            "rfc-3339, iso-8601, rfc-2822, unix"
        ),
    )
    return parser


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "--internal-env-filter":
        return internal_env_filter(argv[2])

    parser = build_argument_parser()
    parse_args = getattr(parser, "parse_intermixed_args", parser.parse_args)
    args = parse_args(argv[1:])

    repo_root = get_repo_root()
    ensure_clean_worktree(repo_root)

    commits = get_commits(repo_root, args.range_expr)
    format_spec = normalize_date_format(args.date_format)

    if args.offset_expr:
        offset_tokens = parse_offset_expression(args.offset_expr)
        updated_dates = build_offset_dates(commits, offset_tokens)
    else:
        updated_dates = collect_edited_dates(repo_root, commits, format_spec)

    preview_lines, mapping = build_preview_lines(commits, updated_dates, format_spec)
    if not preview_lines:
        print("No commit timestamps would change.")
        return 0

    for line in preview_lines:
        print(line)

    if not confirm("Proceed with history rewrite? [y/N]: "):
        print("Aborted.")
        return 1

    run_filter_branch(repo_root, mapping)
    print(f"Rewrote timestamps for {len(mapping)} selected commit(s).")
    return 0


def cli() -> int:
    return main(sys.argv)


def _run_cli() -> None:  # pragma: no cover
    try:
        raise SystemExit(cli())
    except ToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":  # pragma: no cover
    _run_cli()  # pragma: no cover
