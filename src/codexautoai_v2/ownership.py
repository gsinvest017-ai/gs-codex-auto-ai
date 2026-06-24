"""
BUILD-R2 — File Ownership Partitioning (CodexAutoAI v2).

Partitions a list of FN (function-node) build tasks into ordered batches so that:

  1. All FNs targeting the SAME file are grouped into ONE assignment (serialized
     under a single builder — never split across parallel builders).
  2. Within a single batch, every assignment owns a DISJOINT set of files.
  3. Dependency ordering is respected: an FN whose deps have not been assigned
     yet will be delayed to a later batch.

Return value
------------
list[list[dict]]

A list of batches. Each batch is a list of "builder assignments":

    {
        "owner_file": str,          # the file owned by this assignment
        "fns": list[str],           # fn ids in the order they were supplied
    }

Example
-------
    fns = [
        {"id": "A", "file": "src/a.py", "deps": []},
        {"id": "B", "file": "src/b.py", "deps": ["A"]},
        {"id": "C", "file": "src/a.py", "deps": []},   # same file as A
    ]
    result = partition(fns)
    # batch 0: [{"owner_file": "src/a.py", "fns": ["A", "C"]}]
    # batch 1: [{"owner_file": "src/b.py", "fns": ["B"]}]
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any


def partition(fns: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Partition build tasks by file ownership into dependency-ordered batches.

    Parameters
    ----------
    fns:
        List of dicts, each with keys:
          - ``id``   (str): unique identifier for the FN
          - ``file`` (str): source file this FN writes to
          - ``deps`` (list[str]): ids of FNs that must complete before this one

    Returns
    -------
    list[list[dict]]
        Ordered list of batches; each batch is a list of assignments::

            {"owner_file": str, "fns": [fn_id, ...]}

        Within a batch, every ``owner_file`` is unique (disjoint ownership).
        FNs that share a file always end up in the same assignment.
    """
    if not fns:
        return []

    # ------------------------------------------------------------------ #
    # Step 1: group FNs by file                                           #
    # ------------------------------------------------------------------ #
    # file_group[file] = list of fn-ids (preserving input order)
    file_group: dict[str, list[str]] = defaultdict(list)
    fn_by_id: dict[str, dict[str, Any]] = {}
    for fn in fns:
        fn_id = fn["id"]
        fn_file = fn["file"]
        file_group[fn_file].append(fn_id)
        fn_by_id[fn_id] = fn

    # The "file-node" is the unit of scheduling — it represents all FNs
    # that target the same file and must be serialized together.
    # file_node_id is the file path string itself (unique key).

    # ------------------------------------------------------------------ #
    # Step 2: build a dependency graph at the FILE level                  #
    # ------------------------------------------------------------------ #
    # A file-node F depends on file-node G if any FN in F depends on any
    # FN in G (and G != F).

    # fn_id -> file (reverse lookup)
    fn_to_file: dict[str, str] = {}
    for fn in fns:
        fn_to_file[fn["id"]] = fn["file"]

    # file-node → set of file-nodes it depends on
    file_deps: dict[str, set[str]] = {f: set() for f in file_group}
    for fn in fns:
        dep_file = fn_to_file[fn["id"]]
        for dep_id in fn.get("deps", []):
            dep_fn_file = fn_to_file.get(dep_id)
            if dep_fn_file is not None and dep_fn_file != dep_file:
                file_deps[dep_file].add(dep_fn_file)

    # ------------------------------------------------------------------ #
    # Step 3: topological sort of file-nodes (Kahn's algorithm)           #
    # ------------------------------------------------------------------ #
    in_degree: dict[str, int] = {f: 0 for f in file_group}
    dependents: dict[str, list[str]] = defaultdict(list)  # f -> files that depend on f

    for file_node, deps in file_deps.items():
        for dep in deps:
            in_degree[file_node] += 1
            dependents[dep].append(file_node)

    # Start with all file-nodes whose deps are already satisfied
    ready: deque[str] = deque(
        sorted(f for f, deg in in_degree.items() if deg == 0)
    )

    batches: list[list[dict[str, Any]]] = []
    placed: set[str] = set()  # file-nodes already batched

    while ready:
        # All currently-ready file-nodes form one batch (disjoint files).
        batch_files = list(ready)
        ready.clear()

        batch: list[dict[str, Any]] = []
        for file_node in batch_files:
            batch.append(
                {
                    "owner_file": file_node,
                    "fns": list(file_group[file_node]),
                }
            )
            placed.add(file_node)

        batches.append(batch)

        # Unlock dependents whose every dep is now placed
        newly_ready: list[str] = []
        for file_node in batch_files:
            for dependent in dependents[file_node]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    newly_ready.append(dependent)

        # Sort for deterministic ordering
        for f in sorted(newly_ready):
            ready.append(f)

    # If there are nodes not placed (cycle), append them as a final batch
    unplaced = [f for f in file_group if f not in placed]
    if unplaced:
        batches.append(
            [
                {"owner_file": f, "fns": list(file_group[f])}
                for f in sorted(unplaced)
            ]
        )

    return batches
