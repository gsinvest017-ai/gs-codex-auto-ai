"""
ORCH-R6 Topological sort + cycle detection — CodexAutoAI v2

When planning parallel batches, topologically sort the FN dependency
graph.  If a cycle exists, RAISE CycleError with the offending cycle
path reported.  Do not proceed.
"""
from __future__ import annotations


class CycleError(Exception):
    """Raised when a cycle is detected in the dependency graph."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle: list[str] = cycle
        cycle_repr = " -> ".join(cycle)
        super().__init__(f"Cycle detected in dependency graph: {cycle_repr}")


def topological_batches(graph: dict[str, list[str]]) -> list[list[str]]:
    """
    Compute topological batches (Kahn's algorithm) for a dependency graph.

    Parameters
    ----------
    graph:
        Mapping of ``node_id -> [dependency_node_id, ...]``.
        A node listed as a dependency but absent as a key is treated as
        having no dependencies of its own.

    Returns
    -------
    list[list[str]]
        Ordered list of batches.  Batch 0 contains all nodes with no
        unmet dependencies; batch 1 contains nodes whose only deps are
        in batch 0; etc.  Within each batch the order is deterministic
        (sorted lexicographically) so results are reproducible.

    Raises
    ------
    CycleError
        If the graph contains a cycle.  The exception carries the
        ``cycle`` attribute with the path of nodes forming the cycle.
    """
    # Normalise: ensure every node mentioned as a dependency also has
    # an entry in the working graph.
    full_graph: dict[str, list[str]] = {}
    for node, deps in graph.items():
        full_graph.setdefault(node, [])
        full_graph[node] = list(deps)
        for dep in deps:
            full_graph.setdefault(dep, [])

    # Build in-degree map and adjacency list (dep -> dependents).
    in_degree: dict[str, int] = {node: 0 for node in full_graph}
    dependents: dict[str, list[str]] = {node: [] for node in full_graph}

    for node, deps in full_graph.items():
        for dep in deps:
            in_degree[node] += 1
            dependents[dep].append(node)

    # Kahn's BFS — start with nodes that have zero in-degree.
    batches: list[list[str]] = []
    ready = sorted(node for node, deg in in_degree.items() if deg == 0)

    while ready:
        batches.append(list(ready))
        next_ready: list[str] = []
        for node in ready:
            for dependent in dependents[node]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    next_ready.append(dependent)
        ready = sorted(next_ready)

    # If any node still has in-degree > 0, there is a cycle.
    remaining = [n for n, d in in_degree.items() if d > 0]
    if remaining:
        cycle = _find_cycle(full_graph, remaining)
        raise CycleError(cycle)

    return batches


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_cycle(graph: dict[str, list[str]], candidates: list[str]) -> list[str]:
    """
    DFS-based cycle finder.  Returns the cycle path as a list of node
    ids (first == last to make the cycle explicit).
    """
    # Restrict to the subgraph of candidates.
    candidate_set = set(candidates)

    visited: set[str] = set()
    stack: list[str] = []
    on_stack: set[str] = set()

    def dfs(node: str) -> list[str] | None:
        visited.add(node)
        stack.append(node)
        on_stack.add(node)

        for dep in graph.get(node, []):
            if dep not in candidate_set:
                continue
            if dep not in visited:
                result = dfs(dep)
                if result is not None:
                    return result
            elif dep in on_stack:
                # Found the cycle — extract the relevant portion.
                idx = stack.index(dep)
                return stack[idx:] + [dep]

        stack.pop()
        on_stack.discard(node)
        return None

    for start in sorted(candidates):
        if start not in visited:
            result = dfs(start)
            if result is not None:
                return result

    # Fallback (should not happen if caller passes genuine cycle members).
    return list(candidates[:2]) + [candidates[0]]
