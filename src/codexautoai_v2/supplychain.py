"""Supply-chain dependency governance module.

Implements SECGOV-R2: all dependencies must be pinned with a version AND
an integrity hash; unresolvable (hallucinated) packages are blocked.

Pure stdlib. No network calls — resolution is delegated to an injected
callable so tests remain hermetic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Dependency:
    """Parsed representation of a single declared dependency."""

    name: str
    version: Optional[str]
    hash: Optional[str]


@dataclass
class SupplyChainReport:
    """Result returned by :meth:`DependencyController.validate`."""

    ok: bool
    blocked: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)


def _fail_closed_resolver(name: str) -> bool:  # noqa: ARG001
    """Default resolver: fail-closed — every package is treated as unknown."""
    return False


class DependencyController:
    """Validates a list of dependency dicts against SECGOV-R2 rules.

    Parameters
    ----------
    resolver:
        Callable ``(name: str) -> bool``.  Return *True* when the package
        name is confirmed present in a known registry, *False* otherwise.
        Defaults to :func:`_fail_closed_resolver` (fail-closed).
    """

    def __init__(
        self,
        resolver: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self._resolver: Callable[[str], bool] = (
            resolver if resolver is not None else _fail_closed_resolver
        )

    def validate(self, deps: list[dict]) -> SupplyChainReport:
        """Validate *deps* and return a :class:`SupplyChainReport`.

        Each item in *deps* must be a mapping with keys
        ``'name'``, ``'version'``, and ``'hash'``.

        Rejection rules (first matching rule wins for the reason string):

        1. ``version`` missing or empty  → ``'unpinned-version'``
        2. ``hash`` missing or empty     → ``'missing-hash'``
        3. ``resolver(name)`` is False   → ``'unresolvable-package'``
        """
        blocked: list[str] = []
        reasons: dict[str, str] = {}

        for raw in deps:
            dep = Dependency(
                name=raw.get("name", ""),
                version=raw.get("version") or None,
                hash=raw.get("hash") or None,
            )

            if not dep.version:
                blocked.append(dep.name)
                reasons[dep.name] = "unpinned-version"
                continue

            if not dep.hash:
                blocked.append(dep.name)
                reasons[dep.name] = "missing-hash"
                continue

            if not self._resolver(dep.name):
                blocked.append(dep.name)
                reasons[dep.name] = "unresolvable-package"
                continue

        return SupplyChainReport(
            ok=len(blocked) == 0,
            blocked=blocked,
            reasons=reasons,
        )
