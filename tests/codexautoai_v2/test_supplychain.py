"""Tests for SECGOV-R2 supply-chain governance module."""
from __future__ import annotations

import pytest

from src.codexautoai_v2.supplychain import (
    Dependency,
    DependencyController,
    SupplyChainReport,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _allow_all(name: str) -> bool:  # noqa: ARG001
    """Resolver that approves every package name."""
    return True


def _deny_all(name: str) -> bool:  # noqa: ARG001
    """Resolver that rejects every package name (mirrors the default)."""
    return False


def _allow_only(*allowed: str) -> object:
    """Return a resolver that approves only the listed package names."""
    allowed_set = set(allowed)

    def _resolver(name: str) -> bool:
        return name in allowed_set

    return _resolver


VALID_DEP: dict = {"name": "requests", "version": "2.31.0", "hash": "sha256:abc123"}


# ---------------------------------------------------------------------------
# 1. Fully-pinned dep with a truthy resolver  →  ok=True
# ---------------------------------------------------------------------------

class TestFullyPinnedHappyPath:
    def test_single_dep_ok(self) -> None:
        ctrl = DependencyController(resolver=_allow_all)
        report = ctrl.validate([VALID_DEP])

        assert isinstance(report, SupplyChainReport)
        assert report.ok is True
        assert report.blocked == []
        assert report.reasons == {}

    def test_multiple_valid_deps_ok(self) -> None:
        deps = [
            {"name": "requests", "version": "2.31.0", "hash": "sha256:aaa"},
            {"name": "click", "version": "8.1.0", "hash": "sha256:bbb"},
        ]
        ctrl = DependencyController(resolver=_allow_all)
        report = ctrl.validate(deps)

        assert report.ok is True
        assert report.blocked == []

    def test_empty_list_is_ok(self) -> None:
        ctrl = DependencyController(resolver=_allow_all)
        report = ctrl.validate([])

        assert report.ok is True


# ---------------------------------------------------------------------------
# 2. Hallucinated package  →  blocked, reason='unresolvable-package'
# ---------------------------------------------------------------------------

class TestHallucinatedPackage:
    def test_hallucinated_package_blocked(self) -> None:
        ctrl = DependencyController(resolver=_deny_all)
        dep = {"name": "super-fast-json", "version": "1.0.0", "hash": "sha256:xyz"}
        report = ctrl.validate([dep])

        assert report.ok is False
        assert "super-fast-json" in report.blocked
        assert report.reasons["super-fast-json"] == "unresolvable-package"

    def test_known_blocked_unknown_allowed(self) -> None:
        """Mixed: one legit, one hallucinated."""
        resolver = _allow_only("requests")
        ctrl = DependencyController(resolver=resolver)
        deps = [
            {"name": "requests", "version": "2.31.0", "hash": "sha256:ok"},
            {"name": "super-fast-json", "version": "1.0.0", "hash": "sha256:xyz"},
        ]
        report = ctrl.validate(deps)

        assert report.ok is False
        assert "super-fast-json" in report.blocked
        assert "requests" not in report.blocked
        assert report.reasons["super-fast-json"] == "unresolvable-package"


# ---------------------------------------------------------------------------
# 3. Missing version  →  blocked, reason='unpinned-version'
# ---------------------------------------------------------------------------

class TestMissingVersion:
    def test_none_version_blocked(self) -> None:
        ctrl = DependencyController(resolver=_allow_all)
        dep = {"name": "mylib", "version": None, "hash": "sha256:abc"}
        report = ctrl.validate([dep])

        assert report.ok is False
        assert "mylib" in report.blocked
        assert report.reasons["mylib"] == "unpinned-version"

    def test_empty_string_version_blocked(self) -> None:
        ctrl = DependencyController(resolver=_allow_all)
        dep = {"name": "mylib", "version": "", "hash": "sha256:abc"}
        report = ctrl.validate([dep])

        assert report.ok is False
        assert report.reasons["mylib"] == "unpinned-version"

    def test_missing_version_key_blocked(self) -> None:
        ctrl = DependencyController(resolver=_allow_all)
        dep = {"name": "mylib", "hash": "sha256:abc"}  # 'version' key absent
        report = ctrl.validate([dep])

        assert report.ok is False
        assert report.reasons["mylib"] == "unpinned-version"


# ---------------------------------------------------------------------------
# 4. Missing hash  →  blocked, reason='missing-hash'
# ---------------------------------------------------------------------------

class TestMissingHash:
    def test_none_hash_blocked(self) -> None:
        ctrl = DependencyController(resolver=_allow_all)
        dep = {"name": "mylib", "version": "1.0.0", "hash": None}
        report = ctrl.validate([dep])

        assert report.ok is False
        assert "mylib" in report.blocked
        assert report.reasons["mylib"] == "missing-hash"

    def test_empty_string_hash_blocked(self) -> None:
        ctrl = DependencyController(resolver=_allow_all)
        dep = {"name": "mylib", "version": "1.0.0", "hash": ""}
        report = ctrl.validate([dep])

        assert report.ok is False
        assert report.reasons["mylib"] == "missing-hash"

    def test_missing_hash_key_blocked(self) -> None:
        ctrl = DependencyController(resolver=_allow_all)
        dep = {"name": "mylib", "version": "1.0.0"}  # 'hash' key absent
        report = ctrl.validate([dep])

        assert report.ok is False
        assert report.reasons["mylib"] == "missing-hash"


# ---------------------------------------------------------------------------
# 5. Default controller (no resolver arg)  →  fails closed
# ---------------------------------------------------------------------------

class TestDefaultFailClosed:
    def test_default_resolver_blocks_unknown(self) -> None:
        ctrl = DependencyController()  # no resolver → fail-closed
        dep = {"name": "requests", "version": "2.31.0", "hash": "sha256:abc"}
        report = ctrl.validate([dep])

        assert report.ok is False
        assert "requests" in report.blocked
        assert report.reasons["requests"] == "unresolvable-package"

    def test_default_resolver_blocks_everything(self) -> None:
        ctrl = DependencyController()
        deps = [
            {"name": "requests", "version": "2.31.0", "hash": "sha256:aaa"},
            {"name": "click", "version": "8.1.0", "hash": "sha256:bbb"},
        ]
        report = ctrl.validate(deps)

        assert report.ok is False
        assert set(report.blocked) == {"requests", "click"}


# ---------------------------------------------------------------------------
# 6. Rejection priority: version check fires before hash / resolver checks
# ---------------------------------------------------------------------------

class TestRejectionPriority:
    def test_unpinned_version_takes_priority_over_missing_hash(self) -> None:
        ctrl = DependencyController(resolver=_allow_all)
        dep = {"name": "badpkg", "version": None, "hash": None}
        report = ctrl.validate([dep])

        assert report.reasons["badpkg"] == "unpinned-version"

    def test_missing_hash_takes_priority_over_unresolvable(self) -> None:
        ctrl = DependencyController(resolver=_deny_all)
        dep = {"name": "badpkg", "version": "1.0.0", "hash": None}
        report = ctrl.validate([dep])

        # hash check fires before resolver check
        assert report.reasons["badpkg"] == "missing-hash"


# ---------------------------------------------------------------------------
# 7. Dependency dataclass sanity
# ---------------------------------------------------------------------------

class TestDependencyDataclass:
    def test_fields_accessible(self) -> None:
        dep = Dependency(name="foo", version="1.0", hash="sha256:xyz")
        assert dep.name == "foo"
        assert dep.version == "1.0"
        assert dep.hash == "sha256:xyz"

    def test_optional_fields_accept_none(self) -> None:
        dep = Dependency(name="foo", version=None, hash=None)
        assert dep.version is None
        assert dep.hash is None
