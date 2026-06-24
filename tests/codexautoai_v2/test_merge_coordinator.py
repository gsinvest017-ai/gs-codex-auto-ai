"""
test_merge_coordinator.py — Tests for src/codexautoai_v2/merge_coordinator.py

Covers:
  - Disjoint branches (add different files) merge cleanly; ok=True
  - Conflicting branches (same line edited differently) report conflict; ok=False
  - After a conflict-abort the repo is left clean (git status --porcelain empty)

Skips automatically when git is not on PATH.
"""

import subprocess
import shutil
import pytest

from src.codexautoai_v2.merge_coordinator import MergeCoordinator, MergeReport


# ─────────────────────────────────────────────────────────────── skip guard ──

def _git_available() -> bool:
    return shutil.which("git") is not None


pytestmark = pytest.mark.skipif(
    not _git_available(),
    reason="git not found on PATH",
)


# ─────────────────────────────────────────────────────────────────── helpers ──

def git(repo: "os.PathLike", *args: str) -> subprocess.CompletedProcess:
    """Run a git command in *repo*; raise on failure."""
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )


def init_repo(path) -> None:
    """Initialise a fresh git repo with a local user identity."""
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Test User")
    git(path, "config", "user.email", "test@example.com")


def commit_file(repo, filename: str, content: str, message: str) -> None:
    """Write *content* to *filename* inside *repo* and create a commit."""
    target = repo / filename
    target.write_text(content, encoding="utf-8")
    git(repo, "add", filename)
    git(repo, "commit", "-m", message)


def make_branch(repo, branch_name: str, base: str = "main") -> None:
    """Create *branch_name* branching off *base*, without switching to it."""
    git(repo, "branch", branch_name, base)


def checkout(repo, branch: str) -> None:
    git(repo, "checkout", branch)


def porcelain_status(repo) -> str:
    """Return the output of ``git status --porcelain`` (empty = clean)."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


# ──────────────────────────────────────────── fixtures / repo factories ──────

@pytest.fixture()
def disjoint_repo(tmp_path):
    """Repo with main + B1 (adds a1.txt) + B2 (adds a2.txt) — no conflict."""
    repo = tmp_path / "disjoint"
    repo.mkdir()
    init_repo(repo)

    # Initial commit on main
    commit_file(repo, "base.txt", "line1\n", "init: base.txt")

    # B1: adds a1.txt
    make_branch(repo, "B1")
    checkout(repo, "B1")
    commit_file(repo, "a1.txt", "from B1\n", "feat: a1")

    # B2: adds a2.txt (branched from main, not B1)
    checkout(repo, "main")
    make_branch(repo, "B2")
    checkout(repo, "B2")
    commit_file(repo, "a2.txt", "from B2\n", "feat: a2")

    # Leave HEAD on main so MergeCoordinator can checkout main
    checkout(repo, "main")
    return repo


@pytest.fixture()
def conflict_repo(tmp_path):
    """Repo with main + C1 + C2, both editing the same line of base.txt."""
    repo = tmp_path / "conflict"
    repo.mkdir()
    init_repo(repo)

    # Initial commit on main
    commit_file(repo, "base.txt", "shared line\n", "init: base.txt")

    # C1 edits the shared line
    make_branch(repo, "C1")
    checkout(repo, "C1")
    commit_file(repo, "base.txt", "edit from C1\n", "C1: edit base")

    # C2 also edits the same line (branched from main)
    checkout(repo, "main")
    make_branch(repo, "C2")
    checkout(repo, "C2")
    commit_file(repo, "base.txt", "edit from C2\n", "C2: edit base")

    # Leave HEAD on main
    checkout(repo, "main")
    return repo


# ──────────────────────────────────────────────────────────── test cases ─────

class TestDisjointMerge:
    """BUILD-R3-S1 (clean path): disjoint branches merge without conflict."""

    def test_ok_is_true(self, disjoint_repo):
        mc = MergeCoordinator(str(disjoint_repo))
        report = mc.merge_branches(["B1", "B2"])
        assert report.ok is True

    def test_both_branches_in_merged(self, disjoint_repo):
        mc = MergeCoordinator(str(disjoint_repo))
        report = mc.merge_branches(["B1", "B2"])
        assert "B1" in report.merged
        assert "B2" in report.merged

    def test_no_conflicts(self, disjoint_repo):
        mc = MergeCoordinator(str(disjoint_repo))
        report = mc.merge_branches(["B1", "B2"])
        assert report.conflicts == []

    def test_returns_merge_report(self, disjoint_repo):
        mc = MergeCoordinator(str(disjoint_repo))
        report = mc.merge_branches(["B1", "B2"])
        assert isinstance(report, MergeReport)

    def test_merged_files_present_in_main(self, disjoint_repo):
        mc = MergeCoordinator(str(disjoint_repo))
        mc.merge_branches(["B1", "B2"])
        assert (disjoint_repo / "a1.txt").exists()
        assert (disjoint_repo / "a2.txt").exists()


class TestConflictMerge:
    """BUILD-R3 conflict path: conflicting branches are reported, repo stays clean."""

    def test_ok_is_false(self, conflict_repo):
        mc = MergeCoordinator(str(conflict_repo))
        # Merge C1 first (should succeed), then C2 (will conflict with C1)
        report = mc.merge_branches(["C1", "C2"])
        assert report.ok is False

    def test_conflict_recorded(self, conflict_repo):
        mc = MergeCoordinator(str(conflict_repo))
        report = mc.merge_branches(["C1", "C2"])
        assert "C2" in report.conflicts

    def test_first_branch_merged(self, conflict_repo):
        mc = MergeCoordinator(str(conflict_repo))
        report = mc.merge_branches(["C1", "C2"])
        assert "C1" in report.merged

    def test_repo_left_clean_after_abort(self, conflict_repo):
        """After conflict + abort the working tree must be pristine (BUILD-R3)."""
        mc = MergeCoordinator(str(conflict_repo))
        mc.merge_branches(["C1", "C2"])
        status = porcelain_status(conflict_repo)
        assert status == "", f"Repo not clean after abort; git status: {status!r}"

    def test_no_untracked_merge_artifacts(self, conflict_repo):
        """No .orig or MERGE_HEAD files left behind."""
        mc = MergeCoordinator(str(conflict_repo))
        mc.merge_branches(["C1", "C2"])
        merge_head = conflict_repo / ".git" / "MERGE_HEAD"
        assert not merge_head.exists(), "MERGE_HEAD still present — merge not aborted"


class TestMergeReportDataclass:
    """Unit tests for MergeReport standalone behaviour."""

    def test_ok_true_when_no_conflicts(self):
        r = MergeReport(merged=["B1"], conflicts=[])
        assert r.ok is True

    def test_ok_false_when_conflicts(self):
        r = MergeReport(merged=[], conflicts=["C1"])
        assert r.ok is False

    def test_default_empty_lists(self):
        r = MergeReport()
        assert r.merged == []
        assert r.conflicts == []
        assert r.ok is True
