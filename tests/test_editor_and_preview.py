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


def interpolation_commits() -> list[gts.CommitRecord]:
    return [
        gts.CommitRecord(
            full_hash="a" * 40,
            short_hash="aaaa111",
            subject="first commit",
            author_dt=datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC),
            committer_dt=datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC),
        ),
        gts.CommitRecord(
            full_hash="b" * 40,
            short_hash="bbbb222",
            subject="second commit",
            author_dt=datetime(2024, 1, 2, 10, 0, 0, tzinfo=UTC),
            committer_dt=datetime(2024, 1, 2, 10, 0, 0, tzinfo=UTC),
        ),
        gts.CommitRecord(
            full_hash="c" * 40,
            short_hash="cccc333",
            subject="third commit",
            author_dt=datetime(2024, 1, 3, 12, 0, 0, tzinfo=UTC),
            committer_dt=datetime(2024, 1, 3, 12, 0, 0, tzinfo=UTC),
        ),
        gts.CommitRecord(
            full_hash="d" * 40,
            short_hash="dddd444",
            subject="fourth commit",
            author_dt=datetime(2024, 1, 4, 14, 0, 0, tzinfo=UTC),
            committer_dt=datetime(2024, 1, 4, 14, 0, 0, tzinfo=UTC),
        ),
    ]


def test_build_editor_buffer_and_parse_success() -> None:
    commits = sample_commits()
    spec = gts.normalize_date_format("rfc-3339")
    content = gts.build_editor_buffer(commits, spec)
    assert "# Format: rfc-3339" in content
    assert "|" not in content
    assert "# If author and committer times are the same" in content

    edited = content.replace("2024-01-02 11:00:00+00:00", "2024-01-03 12:30:00+00:00", 1)
    edited = edited.replace("2024-01-02 11:06:00+00:00", "2024-01-03 12:45:00+00:00", 1)
    parsed = gts.parse_editor_buffer(edited, commits, spec)
    assert parsed["b" * 40][0].isoformat() == "2024-01-03T12:30:00+00:00"
    assert parsed["b" * 40][1].isoformat() == "2024-01-03T12:45:00+00:00"


def test_build_editor_buffer_and_parse_collapsed_equal_timestamps() -> None:
    equal_dt = datetime(2024, 1, 3, 12, 0, 0, tzinfo=UTC)
    commits = [
        gts.CommitRecord(
            full_hash="c" * 40,
            short_hash="cccc333",
            subject="same timestamp commit",
            author_dt=equal_dt,
            committer_dt=equal_dt,
        )
    ]
    spec = gts.normalize_date_format("rfc-3339")
    content = gts.build_editor_buffer(commits, spec)
    assert "author=" not in content
    assert "committer=" not in content
    assert "2024-01-03 12:00:00+00:00 cccc333 same timestamp commit" in content

    edited = content.replace("2024-01-03 12:00:00+00:00", "2024-01-04 13:30:00+00:00", 1)
    parsed = gts.parse_editor_buffer(edited, commits, spec)
    assert parsed["c" * 40][0].isoformat() == "2024-01-04T13:30:00+00:00"
    assert parsed["c" * 40][1].isoformat() == "2024-01-04T13:30:00+00:00"


def test_parse_editor_buffer_infers_single_missing_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    commits = interpolation_commits()[:3]
    spec = gts.normalize_date_format("rfc-3339")
    monkeypatch.setattr(gts, "build_stable_inference_jitter", lambda *args, **kwargs: gts.timedelta(0))

    edited = "\n".join(
        [
            "2024-01-01 10:00:00+00:00 aaaa111 first commit",
            "bbbb222 second commit",
            "2024-01-03 12:00:00+00:00 cccc333 third commit",
        ]
    )

    parsed = gts.parse_editor_buffer(edited, commits, spec)

    assert parsed["b" * 40][0].isoformat() == "2024-01-02T11:00:00+00:00"
    assert parsed["b" * 40][1].isoformat() == "2024-01-02T11:00:00+00:00"


def test_parse_editor_buffer_infers_multiple_missing_timestamps(monkeypatch: pytest.MonkeyPatch) -> None:
    commits = interpolation_commits()
    spec = gts.normalize_date_format("rfc-3339")
    monkeypatch.setattr(gts, "build_stable_inference_jitter", lambda *args, **kwargs: gts.timedelta(0))

    edited = "\n".join(
        [
            "2024-01-01 10:00:00+00:00 aaaa111 first commit",
            "bbbb222 second commit",
            "cccc333 third commit",
            "2024-01-04 14:00:00+00:00 dddd444 fourth commit",
        ]
    )

    parsed = gts.parse_editor_buffer(edited, commits, spec)

    assert parsed["b" * 40][0].isoformat() == "2024-01-02T11:20:00+00:00"
    assert parsed["b" * 40][1].isoformat() == "2024-01-02T11:20:00+00:00"
    assert parsed["c" * 40][0].isoformat() == "2024-01-03T12:40:00+00:00"
    assert parsed["c" * 40][1].isoformat() == "2024-01-03T12:40:00+00:00"


@pytest.mark.parametrize(
    ("edited", "message"),
    [
        ("bad line", "could not parse edited line"),
        ("author=2024-01-01 10:00:00+00:00 committer=2024-01-01 10:05:00+00:00 deadbee first commit", "unknown short hash"),
        ("author=2024-01-01 10:00:00+00:00 committer=2024-01-01 10:05:00+00:00 aaaa111 changed subject", "commit subject changed"),
        ("author=2024-01-01 10:00:00+00:00 committer=2024-01-01 10:05:00+00:00 aaaa111 second commit", "commit subject changed"),
        ("2024-01-01 10:00:00+00:00 committer=2024-01-01 10:05:00+00:00 aaaa111 first commit", "could not parse edited line"),
        ("author=2024-01-01 10:00:00+00:00 aaaa111 first commit", "could not parse edited line"),
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


def test_parse_editor_buffer_missing_edge_timestamp_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    commits = interpolation_commits()[:3]
    spec = gts.normalize_date_format("rfc-3339")
    monkeypatch.setattr(gts, "build_stable_inference_jitter", lambda *args, **kwargs: gts.timedelta(0))
    edited = "\n".join(
        [
            "2024-01-01 10:00:00+00:00 aaaa111 first commit",
            "2024-01-02 10:00:00+00:00 bbbb222 second commit",
            "cccc333 third commit",
        ]
    )
    with pytest.raises(gts.ToolError, match="cannot infer author time"):
        gts.parse_editor_buffer(edited, commits, spec)


def test_build_stable_inference_jitter_bounds() -> None:
    jitter = gts.build_stable_inference_jitter("a" * 40, "author", gts.timedelta(hours=10))
    assert abs(jitter.total_seconds()) <= 7200
    assert gts.build_stable_inference_jitter("a" * 40, "author", gts.timedelta(0)) == gts.timedelta(0)


def test_infer_missing_series_requires_increasing_anchors() -> None:
    commits = interpolation_commits()[:3]
    values = [
        datetime(2024, 1, 3, 12, 0, 0, tzinfo=UTC),
        None,
        datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC),
    ]
    with pytest.raises(gts.ToolError, match="surrounding timestamps must be strictly increasing"):
        gts.infer_missing_series(commits, values, field_name="author")


def test_ensure_increasing_series_errors() -> None:
    commits = interpolation_commits()[:2]
    values = [
        datetime(2024, 1, 2, 10, 0, 0, tzinfo=UTC),
        datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC),
    ]
    with pytest.raises(gts.ToolError, match="author times must be strictly increasing"):
        gts.ensure_increasing_series(commits, values, field_name="author")


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


def test_collect_edited_dates_reopens_editor_after_parse_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    commits = interpolation_commits()[:3]
    spec = gts.normalize_date_format("rfc-3339")
    monkeypatch.setattr(gts, "build_stable_inference_jitter", lambda *args, **kwargs: gts.timedelta(0))
    calls = {"count": 0}

    def fake_open_editor(repo_root: str, file_path: str) -> None:
        calls["count"] += 1
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        if calls["count"] == 1:
            content = content.replace("2024-01-03 12:00:00+00:00 cccc333 third commit", "cccc333 third commit", 1)
        else:
            assert "cccc333 third commit" in content
            content = content.replace("cccc333 third commit", "2024-01-03 12:00:00+00:00 cccc333 third commit", 1)
        path.write_text(content, encoding="utf-8")

    monkeypatch.setattr(gts, "open_editor", fake_open_editor)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")

    parsed = gts.collect_edited_dates(str(tmp_path), commits, spec)

    assert calls["count"] == 2
    assert parsed["c" * 40][0].isoformat() == "2024-01-03T12:00:00+00:00"
    assert "cannot infer author time" in capsys.readouterr().out


def test_collect_edited_dates_reopen_declined_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commits = interpolation_commits()[:3]
    spec = gts.normalize_date_format("rfc-3339")

    def fake_open_editor(repo_root: str, file_path: str) -> None:
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        content = content.replace("2024-01-03 12:00:00+00:00 cccc333 third commit", "cccc333 third commit", 1)
        path.write_text(content, encoding="utf-8")

    monkeypatch.setattr(gts, "open_editor", fake_open_editor)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")

    with pytest.raises(gts.ToolError, match="cannot infer author time"):
        gts.collect_edited_dates(str(tmp_path), commits, spec)


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


def test_build_preview_lines_collapses_equal_timestamps() -> None:
    equal_dt = datetime(2024, 1, 3, 12, 0, 0, tzinfo=UTC)
    commits = [
        gts.CommitRecord(
            full_hash="c" * 40,
            short_hash="cccc333",
            subject="same timestamp commit",
            author_dt=equal_dt,
            committer_dt=equal_dt,
        )
    ]
    updated = {"c" * 40: (equal_dt.replace(day=4), equal_dt.replace(day=4))}

    preview_lines, mapping = gts.build_preview_lines(commits, updated, gts.normalize_date_format("rfc-3339"))

    assert preview_lines == [
        "2024-01-03 12:00:00+00:00 → 2024-01-04 12:00:00+00:00 cccc333 same timestamp commit"
    ]
    assert mapping == {
        "c" * 40: {
            "author": "2024-01-04T12:00:00+00:00",
            "committer": "2024-01-04T12:00:00+00:00",
        }
    }


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
