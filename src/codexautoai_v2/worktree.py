"""
worktree.py — Git worktree isolation for parallel builders (BUILD-R1).

Each parallel builder gets its own git worktree with a private HEAD, index, and
working tree. This eliminates lost-update races; conflicts are deferred to merge.

If the project root is not a git repo, a git repo is initialised there (fallback).
If git is not on PATH, WorktreeError is raised immediately during __init__.

Stdlib only: subprocess, pathlib, shutil, tempfile.
No imports from sibling codexautoai_v2 modules.
Windows-safe (uses list-form subprocess args everywhere).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


# ──────────────────────────────────────────────────────────────── exceptions ──


class WorktreeError(Exception):
    """Raised for any worktree management failure."""


# ──────────────────────────────────────────────────────────────── helpers ─────


def _git(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a git sub-command, raise WorktreeError on non-zero exit."""
    cmd = ["git"] + list(args)
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise WorktreeError(
            "git executable not found on PATH. "
            "Install git and ensure it is accessible."
        ) from None
    if result.returncode != 0:
        raise WorktreeError(
            f"git {' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result


def _git_available() -> bool:
    """Return True if `git` is on PATH."""
    try:
        subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        return True
    except FileNotFoundError:
        return False


# ─────────────────────────────────────────────────────────── WorktreeManager ──


class WorktreeManager:
    """
    Manages per-builder git worktrees rooted under <repo_root>/.cw_worktrees/.

    Parameters
    ----------
    repo_root:
        Absolute or relative path to the project directory.
        If it is not a git repository, ``git init`` is run automatically.

    Raises
    ------
    WorktreeError
        If git is not on PATH.
    """

    _WORKTREES_DIR = ".cw_worktrees"
    _BRANCH_PREFIX = "cw/"

    def __init__(self, repo_root: str) -> None:
        if not _git_available():
            raise WorktreeError(
                "git executable not found on PATH. "
                "Install git and ensure it is accessible."
            )

        self._root = Path(repo_root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

        if not self.is_git_repo():
            _git("init", cwd=str(self._root))

        self._wt_base = self._root / self._WORKTREES_DIR
        self._wt_base.mkdir(parents=True, exist_ok=True)

        # Track names created in this session so cleanup() knows what to remove.
        self._created: list[str] = []

    # ────────────────────────────────────────────────── public API ────────────

    def is_git_repo(self) -> bool:
        """Return True if repo_root is inside (or is) a git repository."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=str(self._root),
                capture_output=True,
                text=True,
                check=False,
            )
            return result.returncode == 0
        except FileNotFoundError:
            return False

    def create(self, name: str, base: str = "HEAD") -> str:
        """
        Create a new git worktree at .cw_worktrees/<name> on a new branch cw/<name>.

        Parameters
        ----------
        name:
            Short identifier for this worktree (e.g. ``"builder-A"``).
        base:
            Git ref the new branch is forked from.  Defaults to ``"HEAD"``.
            If the repository has no commits yet, ``base`` is ignored and the
            worktree is created on a fresh orphan branch.

        Returns
        -------
        str
            Absolute path to the new worktree directory.

        Raises
        ------
        WorktreeError
            If the worktree already exists or git reports an error.
        """
        wt_path = self._wt_base / name
        if wt_path.exists():
            raise WorktreeError(
                f"Worktree '{name}' already exists at {wt_path}"
            )

        branch = f"{self._BRANCH_PREFIX}{name}"

        # Check whether the repo has at least one commit.
        has_commits = self._repo_has_commits()

        if has_commits:
            # Normal case: create a new branch from base and attach worktree.
            _git(
                "worktree", "add",
                "-b", branch,
                str(wt_path),
                base,
                cwd=str(self._root),
            )
        else:
            # Empty repo: git worktree add doesn't work without a HEAD commit.
            # Create the directory and initialise a fresh working tree manually.
            wt_path.mkdir(parents=True, exist_ok=True)
            _git("init", cwd=str(wt_path))
            # Point the new repo's branch to the desired name so it is consistent.
            _git("checkout", "-b", branch, cwd=str(wt_path))

        self._created.append(name)
        return str(wt_path)

    def list(self) -> list[str]:
        """
        Return a list of absolute paths for all cw/ worktrees under .cw_worktrees/.

        This reads the filesystem rather than ``git worktree list`` so it works
        even for worktrees created in empty-repo fallback mode.
        """
        if not self._wt_base.exists():
            return []
        return [
            str(child.resolve())
            for child in self._wt_base.iterdir()
            if child.is_dir()
        ]

    def remove(self, name: str) -> None:
        """
        Remove the worktree named *name* and prune the git worktree reference.

        Parameters
        ----------
        name:
            The same identifier passed to :meth:`create`.

        Raises
        ------
        WorktreeError
            If the worktree does not exist.
        """
        wt_path = self._wt_base / name
        if not wt_path.exists():
            raise WorktreeError(
                f"Worktree '{name}' not found at {wt_path}"
            )

        # Best-effort: try to use git to unregister the worktree first.
        # Falls back to plain directory removal if git fails (e.g. fallback mode).
        try:
            _git(
                "worktree", "remove", "--force",
                str(wt_path),
                cwd=str(self._root),
            )
        except WorktreeError:
            shutil.rmtree(wt_path, ignore_errors=True)

        # Always attempt to prune stale worktree metadata.
        try:
            _git("worktree", "prune", cwd=str(self._root))
        except WorktreeError:
            pass  # non-fatal

        if name in self._created:
            self._created.remove(name)

    def cleanup(self) -> None:
        """Remove *all* worktrees created by this manager instance."""
        for name in list(self._created):
            try:
                self.remove(name)
            except WorktreeError:
                # If already removed externally, keep going.
                pass

    # ──────────────────────────────────────────────── private helpers ─────────

    def _repo_has_commits(self) -> bool:
        """Return True if HEAD resolves to at least one commit."""
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=str(self._root),
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
