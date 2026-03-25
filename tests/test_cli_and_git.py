from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

import git_time_shift as gts


def run_git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    complete_env = os.environ.copy()
    if env:
        complete_env.update(env)
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=complete_env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def make_commit(repo: Path, filename: str, content: str, message: str, author: str, committer: str) -> None:
    (repo / filename).write_text(content, encoding="utf-8")
    run_git(repo, "add", filename)
    run_git(
        repo,
        "commit",
        "-m",
        message,
        env={
            "GIT_AUTHOR_DATE": author,
            "GIT_COMMITTER_DATE": committer,
            "GIT_AUTHOR_NAME": "Tester",
            "GIT_AUTHOR_EMAIL": "tester@example.com",
            "GIT_COMMITTER_NAME": "Tester",
            "GIT_COMMITTER_EMAIL": "tester@example.com",
        },
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-q")
    run_git(repo, "config", "user.name", "Tester")
    run_git(repo, "config", "user.email", "tester@example.com")
    make_commit(repo, "file.txt", "one\n", "first", "2024-01-01T10:00:00+00:00", "2024-01-01T10:05:00+00:00")
    make_commit(repo, "file.txt", "two\n", "second", "2024-01-02T11:00:00+00:00", "2024-01-02T11:06:00+00:00")
    return repo


def test_run_command_and_git_wrapper(tmp_path: Path) -> None:
    completed = gts.run_command(["python3", "-c", "print('ok')"])
    assert completed.stdout.strip() == "ok"

    completed = gts.run_command(["python3", "-c", "import sys; sys.exit(2)"], check=False)
    assert completed.returncode == 2

    with pytest.raises(gts.ToolError, match="command failed"):
        gts.run_command(["python3", "-c", "import sys; sys.exit(1)"])

    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-q")
    output = gts.git(["rev-parse", "--git-dir"], cwd=str(repo)).strip()
    assert output == ".git"


def test_repo_helpers(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(git_repo)
    assert gts.get_repo_root() == str(git_repo)
    gts.ensure_clean_worktree(str(git_repo))

    commits = gts.get_commits(str(git_repo), "HEAD~1..HEAD")
    assert [commit.subject for commit in commits] == ["second"]

    (git_repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(gts.ToolError, match="uncommitted changes"):
        gts.ensure_clean_worktree(str(git_repo))

    run_git(git_repo, "add", "dirty.txt")
    run_git(git_repo, "commit", "-m", "dirty")
    with pytest.raises(gts.ToolError, match="range selected no commits"):
        gts.get_commits(str(git_repo), "HEAD..HEAD")


def test_get_commits_skips_blank_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gts, "git", lambda *args, **kwargs: "\n")
    with pytest.raises(gts.ToolError, match="range selected no commits"):
        gts.get_commits("/tmp/does-not-matter", "HEAD")


def test_main_offset_flow(git_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    make_commit(git_repo, "file.txt", "three\n", "third", "2024-01-03T12:00:00+00:00", "2024-01-03T12:09:00+00:00")
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")

    exit_code = gts.main(["git_time_shift.py", "HEAD~2..HEAD", "+1d"])
    assert exit_code == 0

    log_lines = run_git(git_repo, "log", "--format=%s|%aI|%cI", "-2").strip().splitlines()
    parsed = [
        tuple(datetime.fromisoformat(part.replace("Z", "+00:00")).isoformat() if index else part for index, part in enumerate(line.split("|")))
        for line in log_lines
    ]
    assert parsed == [
        ("third", "2024-01-04T12:00:00+00:00", "2024-01-04T12:09:00+00:00"),
        ("second", "2024-01-03T11:00:00+00:00", "2024-01-03T11:06:00+00:00"),
    ]
    assert "Rewrote timestamps for 2 selected commit(s)." in capsys.readouterr().out


def test_main_editor_flow_with_custom_format(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    editor = (
        "python3 -c 'from pathlib import Path; import sys; p=Path(sys.argv[1]); "
        "s=p.read_text(); "
        "s=s.replace(\"author=2024-01-02 11:00:00 +00:00\", \"author=2024-01-05 08:30:00 +00:00\", 1); "
        "s=s.replace(\"committer=2024-01-02 11:06:00 +00:00\", \"committer=2024-01-05 09:45:00 +00:00\", 1); "
        "p.write_text(s)'"
    )
    monkeypatch.setenv("GIT_EDITOR", editor)

    exit_code = gts.main(["git_time_shift.py", "HEAD~1..HEAD", "--format", "+%Y-%m-%d %H:%M:%S %:z"])
    assert exit_code == 0

    line = run_git(git_repo, "log", "--format=%s|%aI|%cI", "-1").strip()
    subject, author, committer = line.split("|")
    assert subject == "second"
    assert datetime.fromisoformat(author.replace("Z", "+00:00")).isoformat() == "2024-01-05T08:30:00+00:00"
    assert datetime.fromisoformat(committer.replace("Z", "+00:00")).isoformat() == "2024-01-05T09:45:00+00:00"


def test_main_no_changes_and_abort(monkeypatch: pytest.MonkeyPatch, git_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(git_repo)
    commits = gts.get_commits(str(git_repo), "HEAD~1..HEAD")
    monkeypatch.setattr(gts, "collect_edited_dates", lambda repo_root, commits_arg, spec: {commits[0].full_hash: (commits[0].author_dt, commits[0].committer_dt)})
    assert gts.main(["git_time_shift.py", "HEAD~1..HEAD"]) == 0
    assert "No commit timestamps would change." in capsys.readouterr().out

    monkeypatch.setattr(gts, "collect_edited_dates", lambda repo_root, commits_arg, spec: {commits[0].full_hash: (commits[0].author_dt.replace(hour=12), commits[0].committer_dt)})
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert gts.main(["git_time_shift.py", "HEAD~1..HEAD"]) == 1
    assert "Aborted." in capsys.readouterr().out


def test_main_internal_env_filter_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    mapping = tmp_path / "mapping.json"
    mapping.write_text('{"abc": {"author": "2024-01-01T00:00:00+00:00", "committer": "2024-01-02T00:00:00+00:00"}}', encoding="utf-8")
    monkeypatch.setenv("GIT_COMMIT", "abc")
    assert gts.main(["git_time_shift.py", "--internal-env-filter", str(mapping)]) == 0
    assert "GIT_AUTHOR_DATE=2024-01-01T00:00:00+00:00" in capsys.readouterr().out


def test_run_filter_branch_cleanup_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    command_calls: list[tuple[list[str], str | None]] = []

    def fake_run_command(args: list[str], *, cwd: str | None = None, env: dict[str, str] | None = None, check: bool = True):
        command_calls.append((args, cwd))
        class Result:
            stdout = ""
        return Result()

    monkeypatch.setattr(gts, "run_command", fake_run_command)
    monkeypatch.setattr(gts, "write_mapping_file", lambda mapping: str(tmp_path / "missing.json"))
    monkeypatch.setattr(gts.os, "unlink", lambda path: (_ for _ in ()).throw(FileNotFoundError(path)))
    gts.run_filter_branch(str(tmp_path), {"abc": {"author": "x", "committer": "y"}})
    assert command_calls


def test_cli_delegates_to_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gts.sys, "argv", ["git_time_shift.py", "HEAD~1..HEAD"])
    monkeypatch.setattr(gts, "main", lambda argv: 7)
    assert gts.cli() == 7
