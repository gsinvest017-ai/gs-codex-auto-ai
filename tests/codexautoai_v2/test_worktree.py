"""
test_worktree.py — Tests for src/codexautoai_v2/worktree.py (BUILD-R1).

All tests use tmp_path for filesystem isolation.
git is required; if not on PATH every test is skipped gracefully.

Covers:
  - WorktreeManager.__init__ on a fresh non-git dir creates a repo
  - is_git_repo returns True after init
  - create() produces real directories
  - list() reports all created worktrees
  - isolation: file written in worktree A is NOT visible in worktree B
  - remove() deletes the directory and removes it from list()
  - cleanup() removes all session-created worktrees
  - WorktreeError raised when worktree name already exists
"""

from __future__ import annotations

import subprocess
import shutil
import pytest

from src.codexautoai_v2.worktree import WorktreeError, WorktreeManager


# ──────────────────────────────────────────────────────────── git skip guard ──


def _git_on_path() -> bool:
    try:
        subprocess.run(
            ["git", "--version"],
            capture_output=True,
            check=False,
        )
        return True
    except FileNotFoundError:
        return False


if not _git_on_path():
    pytest.skip("git not found on PATH — skipping all worktree tests", allow_module_level=True)


# ─────────────────────────────────────────────────────────────── fixtures ─────


def _init_repo(path) -> None:
    """Initialise a git repo with one commit so worktrees can be created."""
    subprocess.run(["git", "init", str(path)], capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(path), capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=str(path), capture_output=True, check=True,
    )
    # Create an initial commit so HEAD is valid.
    readme = path / "README.md"
    readme.write_text("init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=str(path), capture_output=True, check=True,
    )


# ─────────────────────────────────────────────────── non-git dir → auto-init ──


def test_non_git_dir_becomes_git_repo(tmp_path):
    """WorktreeManager.__init__ on a plain dir should init a git repo."""
    bare = tmp_path / "notgit"
    bare.mkdir()
    mgr = WorktreeManager(str(bare))
    assert mgr.is_git_repo(), "directory should be a git repo after WorktreeManager init"


def test_is_git_repo_true_on_real_repo(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _init_repo(repo)
    mgr = WorktreeManager(str(repo))
    assert mgr.is_git_repo()


# ──────────────────────────────────────────────────── create / list ───────────


def test_create_returns_existing_path(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    mgr = WorktreeManager(str(repo))
    wt_path = mgr.create("builder-a")
    from pathlib import Path
    assert Path(wt_path).is_dir(), f"worktree path {wt_path} should be a directory"


def test_two_worktrees_both_listed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    mgr = WorktreeManager(str(repo))
    path_a = mgr.create("builder-a")
    path_b = mgr.create("builder-b")
    listed = mgr.list()
    from pathlib import Path
    assert Path(path_a).resolve() in [Path(p).resolve() for p in listed], \
        "builder-a should appear in list()"
    assert Path(path_b).resolve() in [Path(p).resolve() for p in listed], \
        "builder-b should appear in list()"


def test_list_empty_before_create(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    mgr = WorktreeManager(str(repo))
    assert mgr.list() == []


# ──────────────────────────────────────────────────────── isolation (BUILD-R1-S1) ──


def test_file_written_in_a_not_visible_in_b(tmp_path):
    """Core isolation guarantee: edits in worktree A must NOT appear in worktree B."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    mgr = WorktreeManager(str(repo))

    path_a = mgr.create("builder-a")
    path_b = mgr.create("builder-b")

    from pathlib import Path
    secret = Path(path_a) / "secret_a.txt"
    secret.write_text("only in A", encoding="utf-8")

    b_copy = Path(path_b) / "secret_a.txt"
    assert not b_copy.exists(), \
        "File written in worktree A must NOT be visible in worktree B (isolation violated)"


def test_different_content_in_each_worktree(tmp_path):
    """Each worktree can have a different version of the same filename."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    mgr = WorktreeManager(str(repo))

    path_a = mgr.create("builder-a")
    path_b = mgr.create("builder-b")

    from pathlib import Path
    (Path(path_a) / "output.txt").write_text("version-A", encoding="utf-8")
    (Path(path_b) / "output.txt").write_text("version-B", encoding="utf-8")

    assert (Path(path_a) / "output.txt").read_text() == "version-A"
    assert (Path(path_b) / "output.txt").read_text() == "version-B"


# ─────────────────────────────────────────────────────────── remove ───────────


def test_remove_deletes_directory(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    mgr = WorktreeManager(str(repo))
    wt_path = mgr.create("builder-a")

    mgr.remove("builder-a")
    from pathlib import Path
    assert not Path(wt_path).exists(), "worktree directory should be gone after remove()"


def test_remove_removes_from_list(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    mgr = WorktreeManager(str(repo))
    path_a = mgr.create("builder-a")
    mgr.create("builder-b")

    mgr.remove("builder-a")
    from pathlib import Path
    listed = [Path(p).resolve() for p in mgr.list()]
    assert Path(path_a).resolve() not in listed, \
        "removed worktree should no longer appear in list()"


def test_remove_nonexistent_raises(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    mgr = WorktreeManager(str(repo))
    with pytest.raises(WorktreeError):
        mgr.remove("ghost")


# ──────────────────────────────────────────────────────────── cleanup ─────────


def test_cleanup_removes_all_worktrees(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    mgr = WorktreeManager(str(repo))

    paths = [mgr.create(f"builder-{i}") for i in range(3)]
    mgr.cleanup()

    from pathlib import Path
    for p in paths:
        assert not Path(p).exists(), f"cleanup() should have removed {p}"
    assert mgr.list() == [], "list() should be empty after cleanup()"


def test_cleanup_idempotent(tmp_path):
    """Calling cleanup() twice must not raise."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    mgr = WorktreeManager(str(repo))
    mgr.create("builder-a")
    mgr.cleanup()
    mgr.cleanup()  # second call — must be silent


# ───────────────────────────────────────────────── duplicate create raises ────


def test_create_duplicate_name_raises(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    mgr = WorktreeManager(str(repo))
    mgr.create("builder-a")
    with pytest.raises(WorktreeError):
        mgr.create("builder-a")
