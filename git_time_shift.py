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
from pathlib import Path


LOCAL_TZ = datetime.now().astimezone().tzinfo or timezone.utc
ARROW = "→"


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
    kind: str
    base: str = ""
    precision: str = ""
    custom_format: str = ""


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


def get_commits(repo_root: str, range_expr: str) -> list[CommitRecord]:
    output = git(
        [
            "log",
            "--reverse",
            "--topo-order",
            "--format=%H%x00%h%x00%aI%x00%cI%x00%s",
            range_expr,
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
        return DateFormatSpec(raw="rfc-3339=seconds", kind="selector", base="rfc-3339", precision="seconds")

    spec = raw_spec.strip()
    if not spec:
        return DateFormatSpec(raw="rfc-3339=seconds", kind="selector", base="rfc-3339", precision="seconds")

    if spec.startswith("+"):
        return DateFormatSpec(raw=spec, kind="custom", custom_format=spec[1:])

    if spec.startswith("--"):
        spec = spec[2:]

    normalized = spec.lower()
    normalized = normalized.replace("rfc3339", "rfc-3339").replace("iso8601", "iso-8601")

    if normalized in {"rfc-3339", "iso-8601"}:
        base = normalized
        return DateFormatSpec(raw=spec, kind="selector", base=base, precision="seconds")

    if "=" in normalized:
        base, precision = normalized.split("=", 1)
        if base in {"rfc-3339", "iso-8601"} and precision in {"date", "hours", "minutes", "seconds", "ns"}:
            return DateFormatSpec(raw=spec, kind="selector", base=base, precision=precision)

    raise ToolError(
        "unsupported --format value; use +FORMAT or one of "
        "rfc-3339[=date|hours|minutes|seconds|ns], iso-8601[=date|hours|minutes|seconds|ns]"
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
    if spec.precision == "date":
        return dt.strftime("%Y-%m-%d")

    separator = " " if spec.base == "rfc-3339" else "T"
    date_part = dt.strftime("%Y-%m-%d")

    if spec.precision == "hours":
        time_part = dt.strftime("%H")
    elif spec.precision == "minutes":
        time_part = dt.strftime("%H:%M")
    elif spec.precision == "seconds":
        time_part = dt.strftime("%H:%M:%S")
    elif spec.precision == "ns":
        time_part = f"{dt.strftime('%H:%M:%S')}.{dt.microsecond:06d}000"
    else:
        raise ToolError(f"unsupported precision: {spec.precision}")

    return f"{date_part}{separator}{time_part}{format_offset(dt)}"


def format_custom_datetime(dt: datetime, raw_format: str) -> str:
    tokenized = (
        raw_format.replace("%::z", "__GIT_TIME_SHIFT_TZ_SECOND__")
        .replace("%:z", "__GIT_TIME_SHIFT_TZ_MINUTE__")
        .replace("%N", "__GIT_TIME_SHIFT_NANO__")
    )
    rendered = dt.strftime(tokenized)
    rendered = rendered.replace("__GIT_TIME_SHIFT_TZ_SECOND__", format_offset(dt, include_seconds=True))
    rendered = rendered.replace("__GIT_TIME_SHIFT_TZ_MINUTE__", format_offset(dt))
    rendered = rendered.replace("__GIT_TIME_SHIFT_NANO__", f"{dt.microsecond:06d}000")
    return rendered


def format_datetime(dt: datetime, spec: DateFormatSpec) -> str:
    if spec.kind == "selector":
        return format_standard_datetime(dt, spec)
    return format_custom_datetime(dt, spec.custom_format)


def parse_standard_datetime(text: str, spec: DateFormatSpec) -> datetime:
    value = text.strip()
    if spec.precision == "date":
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=LOCAL_TZ)

    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    if spec.base == "rfc-3339" and " " in value and "T" not in value:
        value = value.replace(" ", "T", 1)

    if spec.precision == "hours":
        return datetime.strptime(value, "%Y-%m-%dT%H%z")
    if spec.precision == "minutes":
        return datetime.strptime(value, "%Y-%m-%dT%H:%M%z")
    if spec.precision == "seconds":
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    if spec.precision == "ns":
        match = re.fullmatch(
            r"(?P<stamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(?P<fraction>\d{1,9})(?P<offset>[+-]\d{2}:\d{2})",
            value,
        )
        if not match:
            raise ToolError(f"invalid nanosecond timestamp: {text}")
        fraction = match.group("fraction")[:6].ljust(6, "0")
        parsed = datetime.strptime(
            f"{match.group('stamp')}.{fraction}{match.group('offset')}",
            "%Y-%m-%dT%H:%M:%S.%f%z",
        )
        return parsed
    raise ToolError(f"unsupported precision: {spec.precision}")


def normalize_fractional_seconds(text: str) -> str:
    match = re.search(r"\.(\d{1,9})", text)
    if not match:
        raise ToolError("custom format uses %N but the value has no fractional seconds")
    fraction = match.group(1)[:6].ljust(6, "0")
    return f"{text[:match.start(1)]}{fraction}{text[match.end(1):]}"


def normalize_custom_datetime(text: str, raw_format: str) -> tuple[str, str]:
    normalized_text = text
    python_format = raw_format

    if "%::z" in raw_format:
        normalized_text = re.sub(r"([+-]\d{2}):(\d{2}):(\d{2})", r"\1\2\3", normalized_text)
        python_format = python_format.replace("%::z", "%z")
    elif "%:z" in raw_format:
        normalized_text = re.sub(r"([+-]\d{2}):(\d{2})(?!:\d{2})", r"\1\2", normalized_text)
        python_format = python_format.replace("%:z", "%z")

    if "%N" in raw_format:
        normalized_text = normalize_fractional_seconds(normalized_text)
        python_format = python_format.replace("%N", "%f")

    return normalized_text, python_format


def parse_custom_datetime(text: str, raw_format: str) -> datetime:
    normalized_text, python_format = normalize_custom_datetime(text.strip(), raw_format)
    parsed = datetime.strptime(normalized_text, python_format)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed


def parse_datetime_value(text: str, spec: DateFormatSpec) -> datetime:
    if spec.kind == "selector":
        return parse_standard_datetime(text, spec)
    return parse_custom_datetime(text, spec.custom_format)


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


def parse_offset_expression(parts: list[str]) -> list[tuple[int, int, str]]:
    expression = " ".join(parts).strip()
    if not expression:
        return []

    tokens: list[tuple[int, int, str]] = []
    index = 0
    pattern = re.compile(r"([+-])\s*(\d+)\s*(y|mo|w|d|h|m|s)")
    while index < len(expression):
        while index < len(expression) and expression[index].isspace():
            index += 1
        match = pattern.match(expression, index)
        if not match:
            raise ToolError(
                "invalid offset; use tokens like +1d -10h +30m +2mo"
            )
        sign = 1 if match.group(1) == "+" else -1
        amount = int(match.group(2))
        unit = match.group(3)
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
            f"author={author_text} | committer={committer_text} {commit.short_hash} {commit.subject}"
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
    pattern = re.compile(
        r"^author=(?P<author>.+?) \| committer=(?P<committer>.+?) (?P<short>[0-9a-f]+) (?P<subject>.*)$"
    )

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = pattern.fullmatch(line)
        if not match:
            raise ToolError(f"could not parse edited line: {raw_line}")
        short_hash = match.group("short")
        commit = by_short_hash.get(short_hash)
        if commit is None:
            raise ToolError(f"unknown short hash in edited file: {short_hash}")
        if commit.subject != match.group("subject"):
            raise ToolError(f"commit subject changed for {short_hash}; only edit the timestamps")
        if short_hash in updated:
            raise ToolError(f"duplicate short hash in edited file: {short_hash}")
        author_dt = parse_datetime_value(match.group("author"), spec)
        committer_dt = parse_datetime_value(match.group("committer"), spec)
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
        old_text = (
            f"author={format_datetime(commit.author_dt, spec)} | "
            f"committer={format_datetime(commit.committer_dt, spec)}"
        )
        new_text = (
            f"author={format_datetime(new_author, spec)} | "
            f"committer={format_datetime(new_committer, spec)}"
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
        "offset_parts",
        nargs="*",
        help="optional offset expression, for example: +1d -10h +30m",
    )
    parser.add_argument(
        "--format",
        dest="date_format",
        default="rfc-3339=seconds",
        help=(
            "display/edit format. Supports +FORMAT and selectors like "
            "rfc-3339=seconds or iso-8601=minutes"
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

    if args.offset_parts:
        offset_tokens = parse_offset_expression(args.offset_parts)
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
