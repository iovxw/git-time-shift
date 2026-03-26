from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

import git_time_shift as gts


UTC = timezone.utc


def sample_commits() -> list[gts.CommitRecord]:
    return [
        gts.CommitRecord(
            full_hash="a" * 40,
            short_hash="aaaa111",
            subject="first commit",
            author_dt=datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC),
            committer_dt=datetime(2024, 1, 1, 10, 5, 0, tzinfo=UTC),
        ),
        gts.CommitRecord(
            full_hash="b" * 40,
            short_hash="bbbb222",
            subject="second commit",
            author_dt=datetime(2024, 1, 2, 11, 0, 0, tzinfo=UTC),
            committer_dt=datetime(2024, 1, 2, 11, 6, 0, tzinfo=UTC),
        ),
    ]


def test_build_editor_buffer_and_parse_success() -> None:
    commits = sample_commits()
    spec = gts.normalize_date_format("rfc-3339")
    content = gts.build_editor_buffer(commits, spec)
    assert "# Format: rfc-3339" in content
    assert "|" not in content

    edited = content.replace("2024-01-02 11:00:00+00:00", "2024-01-03 12:30:00+00:00", 1)
    edited = edited.replace("2024-01-02 11:06:00+00:00", "2024-01-03 12:45:00+00:00", 1)
    parsed = gts.parse_editor_buffer(edited, commits, spec)
    assert parsed["b" * 40][0].isoformat() == "2024-01-03T12:30:00+00:00"
    assert parsed["b" * 40][1].isoformat() == "2024-01-03T12:45:00+00:00"


@pytest.mark.parametrize(
    ("edited", "message"),
    [
        ("bad line", "could not parse edited line"),
        ("author=2024-01-01 10:00:00+00:00 committer=2024-01-01 10:05:00+00:00 deadbee first commit", "unknown short hash"),
        ("author=2024-01-01 10:00:00+00:00 committer=2024-01-01 10:05:00+00:00 aaaa111 changed subject", "commit subject changed"),
        (
            "\n".join(
                [
                    "author=2024-01-01 10:00:00+00:00 committer=2024-01-01 10:05:00+00:00 aaaa111 first commit",
                    "author=2024-01-01 10:00:00+00:00 committer=2024-01-01 10:05:00+00:00 aaaa111 first commit",
                    "author=2024-01-02 11:00:00+00:00 committer=2024-01-02 11:06:00+00:00 bbbb222 second commit",
                ]
            ),
            "duplicate short hash",
        ),
    ],
)
def test_parse_editor_buffer_errors(edited: str, message: str) -> None:
    commits = sample_commits()
    spec = gts.normalize_date_format("rfc-3339")
    with pytest.raises(gts.ToolError, match=message):
        gts.parse_editor_buffer(edited, commits, spec)


def test_parse_editor_buffer_missing_commit() -> None:
    commits = sample_commits()
    spec = gts.normalize_date_format("rfc-3339")
    edited = "author=2024-01-01 10:00:00+00:00 committer=2024-01-01 10:05:00+00:00 aaaa111 first commit"
    with pytest.raises(gts.ToolError, match="missing commits"):
        gts.parse_editor_buffer(edited, commits, spec)


def test_collect_edited_dates_and_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commits = sample_commits()
    spec = gts.normalize_date_format("rfc-3339")

    def fake_open_editor(repo_root: str, file_path: str) -> None:
        content = Path(file_path).read_text(encoding="utf-8")
        content = content.replace("2024-01-02 11:00:00+00:00", "2024-01-04 08:00:00+00:00", 1)
        content = content.replace("2024-01-02 11:06:00+00:00", "2024-01-04 09:15:00+00:00", 1)
        Path(file_path).write_text(content, encoding="utf-8")

    monkeypatch.setattr(gts, "open_editor", fake_open_editor)
    edited = gts.collect_edited_dates(str(tmp_path), commits, spec)
    assert edited["b" * 40][0].isoformat() == "2024-01-04T08:00:00+00:00"
    assert edited["b" * 40][1].isoformat() == "2024-01-04T09:15:00+00:00"


def test_collect_edited_dates_cleanup_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commits = sample_commits()
    spec = gts.normalize_date_format("rfc-3339")
    removed_paths: list[str] = []

    def fake_open_editor(repo_root: str, file_path: str) -> None:
        Path(file_path).write_text(gts.build_editor_buffer(commits, spec), encoding="utf-8")

    real_unlink = os.unlink

    def fake_unlink(path: str) -> None:
        removed_paths.append(path)
        raise FileNotFoundError(path)

    monkeypatch.setattr(gts, "open_editor", fake_open_editor)
    monkeypatch.setattr(gts.os, "unlink", fake_unlink)
    parsed = gts.collect_edited_dates(str(tmp_path), commits, spec)
    assert parsed["a" * 40][0].isoformat() == "2024-01-01T10:00:00+00:00"
    assert removed_paths
    monkeypatch.setattr(gts.os, "unlink", real_unlink)


def test_build_offset_dates_preview_and_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    commits = sample_commits()
    offset_tokens = gts.parse_offset_expression("1d")
    updated = gts.build_offset_dates(commits, offset_tokens)
    preview_lines, mapping = gts.build_preview_lines(commits, updated, gts.normalize_date_format("rfc-3339"))
    assert len(preview_lines) == 2
    assert all("|" not in line for line in preview_lines)
    assert set(mapping) == {"a" * 40, "b" * 40}

    unchanged, unchanged_mapping = gts.build_preview_lines(
        commits,
        {commit.full_hash: (commit.author_dt, commit.committer_dt) for commit in commits},
        gts.normalize_date_format("rfc-3339"),
    )
    assert unchanged == []
    assert unchanged_mapping == {}

    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    assert gts.confirm("confirm? ")
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert not gts.confirm("confirm? ")


def test_write_mapping_file_and_internal_env_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    mapping = {"abc": {"author": "2024-01-01T00:00:00+00:00", "committer": "2024-01-02T00:00:00+00:00"}}
    path = gts.write_mapping_file(mapping)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data == mapping

    monkeypatch.setenv("GIT_COMMIT", "abc")
    assert gts.internal_env_filter(path) == 0
    output = capsys.readouterr().out
    assert "GIT_AUTHOR_DATE=2024-01-01T00:00:00+00:00" in output

    monkeypatch.setenv("GIT_COMMIT", "missing")
    assert gts.internal_env_filter(path) == 0

    monkeypatch.delenv("GIT_COMMIT")
    with pytest.raises(gts.ToolError, match="GIT_COMMIT is not set"):
        gts.internal_env_filter(path)

    os.unlink(path)


def test_determine_editor_and_open_editor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gts, "git", lambda *args, **kwargs: "nano")
    assert gts.determine_editor(str(tmp_path)) == "nano"

    def raise_tool_error(*args, **kwargs):
        raise gts.ToolError("no git var")

    monkeypatch.setattr(gts, "git", raise_tool_error)
    monkeypatch.delenv("GIT_EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "vim")
    assert gts.determine_editor(str(tmp_path)) == "vim"

    monkeypatch.setattr(gts, "git", lambda *args, **kwargs: "")
    monkeypatch.delenv("EDITOR")
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("GIT_EDITOR", raising=False)
    assert gts.determine_editor(str(tmp_path)) == "vi"

    called: list[str] = []

    def fake_run(command: str, cwd: str | None = None, shell: bool | None = None, check: bool | None = None):
        called.append(command)
        return None

    monkeypatch.setattr(gts.subprocess, "run", fake_run)
    gts.open_editor(str(tmp_path), "file.txt")
    assert called == ["vi file.txt"]


def test_open_editor_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gts, "determine_editor", lambda repo_root: "vim")

    def explode(*args, **kwargs):
        raise gts.subprocess.CalledProcessError(returncode=5, cmd="vim")

    monkeypatch.setattr(gts.subprocess, "run", explode)
    with pytest.raises(gts.ToolError, match="editor exited with status 5"):
        gts.open_editor(str(tmp_path), "file.txt")
