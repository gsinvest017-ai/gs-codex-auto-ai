"""Tests for BUILD-R2 file ownership partitioning (src/codexautoai_v2/ownership.py)."""

import pytest
from src.codexautoai_v2.ownership import partition


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assignments_in_batch(batch):
    """Return dict {owner_file: set(fn_ids)} for a single batch."""
    result = {}
    for assignment in batch:
        owner = assignment["owner_file"]
        assert owner not in result, (
            f"Duplicate owner_file '{owner}' within the same batch — "
            "violates disjoint ownership requirement"
        )
        result[owner] = set(assignment["fns"])
    return result


def _file_for_fn(batches, fn_id):
    """Return (batch_index, owner_file) for a given fn_id across all batches."""
    for batch_idx, batch in enumerate(batches):
        for assignment in batch:
            if fn_id in assignment["fns"]:
                return batch_idx, assignment["owner_file"]
    raise KeyError(f"fn_id '{fn_id}' not found in any batch")


# ---------------------------------------------------------------------------
# Core: same-file FNs are SERIALIZED (BUILD-R2-S1)
# ---------------------------------------------------------------------------

class TestSameFileSerialization:

    def test_two_fns_same_file_end_in_same_assignment(self):
        """FN-1 and FN-2 both target src/utils.py → one assignment, one builder."""
        fns = [
            {"id": "FN-1", "file": "src/utils.py", "deps": []},
            {"id": "FN-2", "file": "src/utils.py", "deps": []},
        ]
        batches = partition(fns)

        # Exactly one assignment for src/utils.py
        all_assignments = [a for batch in batches for a in batch]
        utils_assignments = [a for a in all_assignments if a["owner_file"] == "src/utils.py"]
        assert len(utils_assignments) == 1, (
            f"Expected exactly 1 assignment for src/utils.py, got {len(utils_assignments)}"
        )

        # Both FN ids present in that assignment
        fns_in_assignment = set(utils_assignments[0]["fns"])
        assert "FN-1" in fns_in_assignment
        assert "FN-2" in fns_in_assignment

    def test_same_file_never_split_across_parallel_builders(self):
        """Verify no batch contains two separate assignments for the same file."""
        fns = [
            {"id": "A", "file": "src/utils.py", "deps": []},
            {"id": "B", "file": "src/utils.py", "deps": []},
            {"id": "C", "file": "src/utils.py", "deps": []},
        ]
        batches = partition(fns)
        for i, batch in enumerate(batches):
            owners = [a["owner_file"] for a in batch]
            assert len(owners) == len(set(owners)), (
                f"Batch {i} has duplicate owner_files: {owners}"
            )

    def test_three_files_same_owner_across_all_batches(self):
        """All FNs for a file must appear in exactly ONE assignment total."""
        fns = [
            {"id": "X1", "file": "lib/x.py", "deps": []},
            {"id": "X2", "file": "lib/x.py", "deps": []},
            {"id": "Y1", "file": "lib/y.py", "deps": []},
        ]
        batches = partition(fns)
        file_assignment_count: dict[str, int] = {}
        for batch in batches:
            for a in batch:
                file_assignment_count[a["owner_file"]] = (
                    file_assignment_count.get(a["owner_file"], 0) + 1
                )
        for owner_file, count in file_assignment_count.items():
            assert count == 1, (
                f"File '{owner_file}' appeared in {count} assignments (must be 1)"
            )


# ---------------------------------------------------------------------------
# Disjoint files within a batch
# ---------------------------------------------------------------------------

class TestDisjointFilesWithinBatch:

    def test_independent_fns_different_files_same_batch(self):
        """FNs on different files with no deps → same batch, disjoint files."""
        fns = [
            {"id": "A", "file": "src/a.py", "deps": []},
            {"id": "B", "file": "src/b.py", "deps": []},
            {"id": "C", "file": "src/c.py", "deps": []},
        ]
        batches = partition(fns)
        assert len(batches) == 1, f"Expected 1 batch, got {len(batches)}: {batches}"
        batch_map = _assignments_in_batch(batches[0])
        assert "src/a.py" in batch_map
        assert "src/b.py" in batch_map
        assert "src/c.py" in batch_map

    def test_no_duplicate_owner_file_within_any_batch(self):
        """Invariant: no batch ever holds two assignments with the same owner_file."""
        fns = [
            {"id": "F1", "file": "m1.py", "deps": []},
            {"id": "F2", "file": "m2.py", "deps": ["F1"]},
            {"id": "F3", "file": "m1.py", "deps": []},  # same file as F1
            {"id": "F4", "file": "m3.py", "deps": []},
        ]
        batches = partition(fns)
        for idx, batch in enumerate(batches):
            owners = [a["owner_file"] for a in batch]
            assert len(owners) == len(set(owners)), (
                f"Batch {idx} has non-disjoint owner_files: {owners}"
            )

    def test_single_fn_single_batch(self):
        fns = [{"id": "Solo", "file": "only.py", "deps": []}]
        batches = partition(fns)
        assert len(batches) == 1
        assert batches[0][0]["owner_file"] == "only.py"
        assert batches[0][0]["fns"] == ["Solo"]


# ---------------------------------------------------------------------------
# Dependency ordering
# ---------------------------------------------------------------------------

class TestDependencyOrdering:

    def test_dep_in_later_batch(self):
        """FN-C depends on FN-A → C must be in a strictly later batch than A."""
        fns = [
            {"id": "FN-A", "file": "src/a.py", "deps": []},
            {"id": "FN-C", "file": "src/c.py", "deps": ["FN-A"]},
        ]
        batches = partition(fns)
        idx_a, _ = _file_for_fn(batches, "FN-A")
        idx_c, _ = _file_for_fn(batches, "FN-C")
        assert idx_c > idx_a, (
            f"FN-C (batch {idx_c}) should be after FN-A (batch {idx_a})"
        )

    def test_chain_ordering(self):
        """A → B → C must appear in 3 separate layers."""
        fns = [
            {"id": "A", "file": "a.py", "deps": []},
            {"id": "B", "file": "b.py", "deps": ["A"]},
            {"id": "C", "file": "c.py", "deps": ["B"]},
        ]
        batches = partition(fns)
        idx_a, _ = _file_for_fn(batches, "A")
        idx_b, _ = _file_for_fn(batches, "B")
        idx_c, _ = _file_for_fn(batches, "C")
        assert idx_a < idx_b < idx_c

    def test_fan_out_deps(self):
        """A has no deps; B and C both depend on A → B and C may be in the same batch."""
        fns = [
            {"id": "A", "file": "a.py", "deps": []},
            {"id": "B", "file": "b.py", "deps": ["A"]},
            {"id": "C", "file": "c.py", "deps": ["A"]},
        ]
        batches = partition(fns)
        idx_a, _ = _file_for_fn(batches, "A")
        idx_b, _ = _file_for_fn(batches, "B")
        idx_c, _ = _file_for_fn(batches, "C")
        assert idx_b > idx_a
        assert idx_c > idx_a
        # B and C can be in the same batch (no dep between them)
        assert idx_b == idx_c, "B and C are independent; they should be co-batched"

    def test_same_file_deps_within_group(self):
        """FN-1 and FN-2 both write src/utils.py; FN-2 depends on FN-1.
        They must still be in the SAME assignment (serialized).
        The dep is within the file-group — no cross-file ordering needed."""
        fns = [
            {"id": "FN-1", "file": "src/utils.py", "deps": []},
            {"id": "FN-2", "file": "src/utils.py", "deps": ["FN-1"]},
        ]
        batches = partition(fns)
        all_assignments = [a for batch in batches for a in batch]
        utils_assignments = [a for a in all_assignments if a["owner_file"] == "src/utils.py"]
        assert len(utils_assignments) == 1
        fns_set = set(utils_assignments[0]["fns"])
        assert fns_set == {"FN-1", "FN-2"}

    def test_mixed_scenario(self):
        """Comprehensive: two FNs share a file; a third FN depends on that group."""
        fns = [
            {"id": "A1", "file": "src/a.py", "deps": []},
            {"id": "A2", "file": "src/a.py", "deps": []},   # same file as A1
            {"id": "B",  "file": "src/b.py", "deps": ["A1"]},
        ]
        batches = partition(fns)
        # A1 and A2 must be in the same batch (batch 0)
        idx_a1, file_a1 = _file_for_fn(batches, "A1")
        idx_a2, file_a2 = _file_for_fn(batches, "A2")
        idx_b,  _       = _file_for_fn(batches, "B")

        assert idx_a1 == idx_a2, "A1 and A2 must be in the same batch"
        assert file_a1 == file_a2 == "src/a.py"
        assert idx_b > idx_a1, "B must come after A1"

    def test_no_deps_all_in_first_batch(self):
        """With no deps at all, everything collapses into one batch."""
        fns = [
            {"id": str(i), "file": f"f{i}.py", "deps": []}
            for i in range(5)
        ]
        batches = partition(fns)
        assert len(batches) == 1
        assert len(batches[0]) == 5


# ---------------------------------------------------------------------------
# No file appears under two assignments in the same batch (explicit check)
# ---------------------------------------------------------------------------

class TestNoFileInTwoAssignmentsPerBatch:

    def test_explicit_disjoint_invariant(self):
        """Assert no file appears under two assignments in the same batch — ever."""
        fns = [
            {"id": "P", "file": "shared.py", "deps": []},
            {"id": "Q", "file": "shared.py", "deps": []},
            {"id": "R", "file": "other.py",  "deps": []},
            {"id": "S", "file": "other2.py", "deps": ["P"]},
        ]
        batches = partition(fns)
        for batch_idx, batch in enumerate(batches):
            seen_files: set[str] = set()
            for assignment in batch:
                f = assignment["owner_file"]
                assert f not in seen_files, (
                    f"File '{f}' appears in two assignments in batch {batch_idx}"
                )
                seen_files.add(f)

    def test_large_shared_file_group(self):
        """5 FNs all target the same file → 1 assignment, 1 batch entry."""
        fns = [{"id": f"FN-{i}", "file": "big.py", "deps": []} for i in range(5)]
        batches = partition(fns)
        # Should be exactly one assignment total
        all_assignments = [a for batch in batches for a in batch]
        assert len(all_assignments) == 1
        assert all_assignments[0]["owner_file"] == "big.py"
        assert len(all_assignments[0]["fns"]) == 5


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_empty_input(self):
        assert partition([]) == []

    def test_single_fn_no_deps(self):
        fns = [{"id": "only", "file": "x.py", "deps": []}]
        batches = partition(fns)
        assert len(batches) == 1
        assert batches[0][0] == {"owner_file": "x.py", "fns": ["only"]}

    def test_all_fns_in_correct_batches(self):
        """Every fn_id must appear in exactly one assignment across all batches."""
        fns = [
            {"id": "X", "file": "x.py", "deps": []},
            {"id": "Y", "file": "x.py", "deps": []},
            {"id": "Z", "file": "z.py", "deps": ["X"]},
        ]
        batches = partition(fns)
        all_fn_ids = [fn_id for batch in batches for a in batch for fn_id in a["fns"]]
        assert sorted(all_fn_ids) == sorted(["X", "Y", "Z"])
        # No duplicates
        assert len(all_fn_ids) == len(set(all_fn_ids))
