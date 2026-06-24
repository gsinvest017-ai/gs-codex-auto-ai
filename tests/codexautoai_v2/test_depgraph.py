"""
Tests for ORCH-R6 Topological sort + cycle detection (depgraph.py).

Coverage:
- simple cycle A->B->A raises CycleError with the cycle path
- three-node cycle A->B->C->A raises CycleError
- self-loop A->A raises CycleError
- valid DAG returns correct batches
- independent nodes in same batch
- dependency ordering respected across batches
- empty graph returns empty list
- single node with no deps
- diamond dependency shape
"""
import pytest

from src.codexautoai_v2.depgraph import CycleError, topological_batches


# ---------------------------------------------------------------------------
# Cycle detection tests
# ---------------------------------------------------------------------------


class TestCycleDetection:
    def test_simple_two_node_cycle_raises(self):
        graph = {"A": ["B"], "B": ["A"]}

        with pytest.raises(CycleError) as exc_info:
            topological_batches(graph)

        err = exc_info.value
        assert isinstance(err.cycle, list)
        assert len(err.cycle) >= 2
        # The cycle should contain both A and B.
        cycle_set = set(err.cycle)
        assert "A" in cycle_set
        assert "B" in cycle_set

    def test_cycle_first_equals_last_forming_closed_loop(self):
        graph = {"A": ["B"], "B": ["A"]}

        with pytest.raises(CycleError) as exc_info:
            topological_batches(graph)

        cycle = exc_info.value.cycle
        # DFS cycle representation: first == last
        assert cycle[0] == cycle[-1], "Cycle path must be closed (first == last)"

    def test_three_node_cycle_raises(self):
        graph = {"A": ["B"], "B": ["C"], "C": ["A"]}

        with pytest.raises(CycleError) as exc_info:
            topological_batches(graph)

        cycle_set = set(exc_info.value.cycle)
        assert cycle_set >= {"A", "B", "C"}

    def test_self_loop_raises(self):
        graph = {"A": ["A"]}

        with pytest.raises(CycleError) as exc_info:
            topological_batches(graph)

        assert "A" in exc_info.value.cycle

    def test_cycle_error_stores_cycle_attribute(self):
        graph = {"X": ["Y"], "Y": ["X"]}

        with pytest.raises(CycleError) as exc_info:
            topological_batches(graph)

        assert hasattr(exc_info.value, "cycle")
        assert isinstance(exc_info.value.cycle, list)

    def test_partial_cycle_in_larger_graph(self):
        """A graph where C->D->C cycle exists alongside A, B that are fine."""
        graph = {
            "A": [],
            "B": ["A"],
            "C": ["D"],
            "D": ["C"],
        }

        with pytest.raises(CycleError):
            topological_batches(graph)

    def test_cycle_error_message_contains_cycle_nodes(self):
        graph = {"A": ["B"], "B": ["A"]}

        with pytest.raises(CycleError) as exc_info:
            topological_batches(graph)

        msg = str(exc_info.value)
        assert "A" in msg
        assert "B" in msg


# ---------------------------------------------------------------------------
# Valid DAG tests
# ---------------------------------------------------------------------------


class TestValidDAG:
    def test_independent_nodes_in_batch_zero(self):
        """A and B have no deps -> both in batch 0; C depends on A,B -> batch 1."""
        graph = {"A": [], "B": [], "C": ["A", "B"]}

        batches = topological_batches(graph)

        assert len(batches) == 2
        # Batch 0 must contain A and B (order within batch may vary).
        assert set(batches[0]) == {"A", "B"}
        assert batches[1] == ["C"]

    def test_linear_chain(self):
        """A -> B -> C produces three batches."""
        graph = {"A": [], "B": ["A"], "C": ["B"]}

        batches = topological_batches(graph)

        assert len(batches) == 3
        assert batches[0] == ["A"]
        assert batches[1] == ["B"]
        assert batches[2] == ["C"]

    def test_empty_graph(self):
        batches = topological_batches({})
        assert batches == []

    def test_single_node_no_deps(self):
        batches = topological_batches({"A": []})
        assert batches == [["A"]]

    def test_diamond_shape(self):
        """
        A has no deps.
        B and C both depend on A.
        D depends on both B and C.
        Expected: [[A], [B, C], [D]]
        """
        graph = {"A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"]}

        batches = topological_batches(graph)

        assert len(batches) == 3
        assert batches[0] == ["A"]
        assert set(batches[1]) == {"B", "C"}
        assert batches[2] == ["D"]

    def test_all_independent_nodes_in_one_batch(self):
        graph = {"X": [], "Y": [], "Z": []}
        batches = topological_batches(graph)
        assert len(batches) == 1
        assert set(batches[0]) == {"X", "Y", "Z"}

    def test_dep_listed_but_not_key_treated_as_no_dep_node(self):
        """
        If a node appears only as a dependency but not as a graph key,
        it should be treated as having no deps (implicit node).
        """
        graph = {"B": ["A"]}  # A is not a key

        batches = topological_batches(graph)

        assert len(batches) == 2
        assert batches[0] == ["A"]
        assert batches[1] == ["B"]

    def test_batches_are_sorted_lexicographically(self):
        """Within a batch, nodes should be sorted for determinism."""
        graph = {"C": [], "A": [], "B": []}
        batches = topological_batches(graph)
        assert batches == [["A", "B", "C"]]

    def test_complex_multi_level(self):
        """
        E depends on C and D.
        C depends on A.
        D depends on B.
        A, B have no deps.
        Expected batches: [[A, B], [C, D], [E]]
        """
        graph = {
            "A": [],
            "B": [],
            "C": ["A"],
            "D": ["B"],
            "E": ["C", "D"],
        }
        batches = topological_batches(graph)
        assert len(batches) == 3
        assert set(batches[0]) == {"A", "B"}
        assert set(batches[1]) == {"C", "D"}
        assert batches[2] == ["E"]
