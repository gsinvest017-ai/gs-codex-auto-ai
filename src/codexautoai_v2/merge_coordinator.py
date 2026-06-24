"""
merge_coordinator.py — 3-way merge coordinator for CodexAutoAI v2 parallel builds.

Implements:
  BUILD-R3: When a parallel build batch completes, merge each worktree branch
            back via 3-way merge; if a conflict arises, REPORT it (do NOT
            silently overwrite).

API:
  MergeReport  — result dataclass: merged, conflicts, ok
  MergeCoordinator — merge_branches(branches, into='main') -> MergeReport
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MergeReport:
    """Result of a merge_branches call.

    merged    — branch names that merged cleanly.
    conflicts — branch names that produced a conflict (merge was aborted).
    ok        — True when conflicts is empty.
    """

    merged: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.conflicts) == 0


class MergeCoordinator:
    """Coordinate 3-way merges from parallel-build worktree branches.

    Parameters
    ----------
    repo_root:
        Absolute path to the git repository root.  Must be a valid git repo
        before any call to merge_branches().
    """

    def __init__(self, repo_root: str) -> None:
        self._root = Path(repo_root)

    # ------------------------------------------------------------------ public

    def merge_branches(
        self,
        branches: list[str],
        into: str = "main",
    ) -> MergeReport:
        """Attempt to merge each branch in *branches* into *into*.

        For each branch:
        - Run ``git merge --no-ff --no-edit <branch>``.
        - If git exits 0  → record in MergeReport.merged.
        - If git exits non-0 (conflict) → run ``git merge --abort``,
          record in MergeReport.conflicts.

        The target branch (*into*) is checked out before the first merge.

        Returns
        -------
        MergeReport
            ok is True only when every branch merged without conflicts.
        """
        report = MergeReport()

        # Checkout the integration branch first
        self._git("checkout", into)

        for branch in branches:
            result = self._run_merge(branch)
            if result.returncode == 0:
                report.merged.append(branch)
            else:
                # Conflict detected — abort to leave the repo clean
                self._git("merge", "--abort")
                report.conflicts.append(branch)

        return report

    # ----------------------------------------------------------------- private

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        """Run a git command in the repo root; raise on failure."""
        return subprocess.run(
            ["git", *args],
            cwd=str(self._root),
            capture_output=True,
            text=True,
            check=True,
        )

    def _run_merge(self, branch: str) -> subprocess.CompletedProcess:
        """Run ``git merge --no-ff --no-edit <branch>``; return result without raising."""
        return subprocess.run(
            ["git", "merge", "--no-ff", "--no-edit", branch],
            cwd=str(self._root),
            capture_output=True,
            text=True,
        )
