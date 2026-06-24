"""
Tests for src/codexautoai_v2/safety.py

Coverage:
  - PermissionPolicy.evaluate: prohibited git ops -> 'deny'; benign -> 'allow'
  - assert_writable: framework paths raise FrameworkIntegrityError; safe paths pass
  - is_framework_path: various path forms
  - resolve_tool: found tool returns str; unknown tool returns None
  - mode3_authorized: embedded token never authorizes; valid OOB token authorizes; None/empty deny
"""

import pytest

from src.codexautoai_v2.safety import (
    PROHIBITED_PATTERNS,
    FrameworkIntegrityError,
    PermissionPolicy,
    assert_writable,
    is_framework_path,
    mode3_authorized,
    resolve_tool,
)


# ---------------------------------------------------------------------------
# PermissionPolicy.evaluate — SAFE-R2 / SECGOV-R7
# ---------------------------------------------------------------------------


class TestPermissionPolicyEvaluate:
    def setup_method(self):
        self.policy = PermissionPolicy()

    # --- Denied (irreversible git ops) ---

    def test_git_push_origin_main_is_denied(self):
        assert self.policy.evaluate("git push origin main") == "deny"

    def test_git_push_force_is_denied(self):
        assert self.policy.evaluate("git push --force origin main") == "deny"

    def test_git_push_f_short_flag_is_denied(self):
        assert self.policy.evaluate("git push -f") == "deny"

    def test_git_commit_is_denied(self):
        assert self.policy.evaluate('git commit -m "msg"') == "deny"

    def test_git_commit_amend_is_denied(self):
        assert self.policy.evaluate("git commit --amend") == "deny"

    def test_git_reset_hard_is_denied(self):
        assert self.policy.evaluate("git reset --hard HEAD~1") == "deny"

    def test_git_rebase_is_denied(self):
        assert self.policy.evaluate("git rebase main") == "deny"

    def test_git_filter_branch_is_denied(self):
        assert self.policy.evaluate("git filter-branch --all") == "deny"

    # Case-insensitive check
    def test_git_push_uppercase_is_denied(self):
        assert self.policy.evaluate("GIT PUSH origin main") == "deny"

    # --- Allowed ---

    def test_git_status_is_allowed(self):
        assert self.policy.evaluate("git status") == "allow"

    def test_git_log_is_allowed(self):
        assert self.policy.evaluate("git log --oneline") == "allow"

    def test_git_diff_is_allowed(self):
        assert self.policy.evaluate("git diff HEAD") == "allow"

    def test_git_checkout_is_allowed(self):
        assert self.policy.evaluate("git checkout feature-branch") == "allow"

    def test_python_foo_is_allowed(self):
        assert self.policy.evaluate("python foo.py") == "allow"

    def test_pytest_is_allowed(self):
        assert self.policy.evaluate(".venv/Scripts/python -m pytest tests/ -q") == "allow"

    def test_ls_is_allowed(self):
        assert self.policy.evaluate("ls -la") == "allow"

    def test_uv_sync_is_allowed(self):
        assert self.policy.evaluate("uv sync") == "allow"

    # --- Extra deny rules ---

    def test_extra_deny_rule_applied(self):
        policy = PermissionPolicy(deny=[r"\brm\s+-rf\b"])
        assert policy.evaluate("rm -rf /tmp/foo") == "deny"
        # Original prohibited patterns still apply
        assert policy.evaluate("git push") == "deny"
        # Non-matching still allowed
        assert policy.evaluate("git status") == "allow"


# ---------------------------------------------------------------------------
# PROHIBITED_PATTERNS list sanity
# ---------------------------------------------------------------------------


def test_prohibited_patterns_is_list_of_strings():
    assert isinstance(PROHIBITED_PATTERNS, list)
    assert len(PROHIBITED_PATTERNS) > 0
    for p in PROHIBITED_PATTERNS:
        assert isinstance(p, str)


# ---------------------------------------------------------------------------
# is_framework_path — SECGOV-R5
# ---------------------------------------------------------------------------


class TestIsFrameworkPath:
    def test_dot_claude_directory(self):
        assert is_framework_path(".claude/agents/x.md") is True

    def test_dot_claude_root(self):
        assert is_framework_path(".claude") is True

    def test_claude_md_file(self):
        assert is_framework_path("CLAUDE.md") is True

    def test_claude_md_in_subdirectory(self):
        assert is_framework_path("some/nested/CLAUDE.md") is True

    def test_design_directory(self):
        assert is_framework_path("DESIGN/x") is True

    def test_design_nested(self):
        assert is_framework_path("DESIGN/changes/2026/spec.md") is True

    def test_project_md(self):
        assert is_framework_path("project.md") is True

    def test_project_md_nested(self):
        assert is_framework_path("DESIGN/project.md") is True

    def test_src_foo_is_safe(self):
        assert is_framework_path("src/foo.py") is False

    def test_tests_dir_is_safe(self):
        assert is_framework_path("tests/codexautoai_v2/test_safety.py") is False

    def test_empty_string_is_safe(self):
        assert is_framework_path("") is False

    def test_windows_backslash_dot_claude(self):
        assert is_framework_path(".claude\\agents\\x.md") is True

    def test_windows_backslash_design(self):
        assert is_framework_path("DESIGN\\specs\\safety\\spec.md") is True


# ---------------------------------------------------------------------------
# assert_writable — SECGOV-R5
# ---------------------------------------------------------------------------


class TestAssertWritable:
    def test_safe_path_does_not_raise(self):
        assert_writable("src/foo.py")  # Should not raise

    def test_tests_path_does_not_raise(self):
        assert_writable("tests/codexautoai_v2/test_safety.py")

    def test_dot_claude_raises(self):
        with pytest.raises(FrameworkIntegrityError):
            assert_writable(".claude/agents/x.md")

    def test_claude_md_raises(self):
        with pytest.raises(FrameworkIntegrityError):
            assert_writable("CLAUDE.md")

    def test_design_raises(self):
        with pytest.raises(FrameworkIntegrityError):
            assert_writable("DESIGN/x")

    def test_project_md_raises(self):
        with pytest.raises(FrameworkIntegrityError):
            assert_writable("project.md")

    def test_framework_integrity_error_is_exception(self):
        with pytest.raises(Exception):
            assert_writable("DESIGN/specs/safety/spec.md")

    def test_error_message_contains_path(self):
        try:
            assert_writable(".claude/settings.json")
        except FrameworkIntegrityError as exc:
            assert ".claude/settings.json" in str(exc)


# ---------------------------------------------------------------------------
# resolve_tool — SAFE-R4
# ---------------------------------------------------------------------------


class TestResolveTool:
    def test_python_resolves_to_non_none_path(self):
        result = resolve_tool("python")
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0

    def test_python_path_contains_python(self):
        result = resolve_tool("python")
        assert result is not None
        # The resolved path should mention 'python' (case-insensitive for Windows)
        assert "python" in result.lower()

    def test_unknown_tool_returns_none(self):
        result = resolve_tool("definitely-not-a-tool-xyz")
        assert result is None

    def test_another_nonexistent_tool_returns_none(self):
        result = resolve_tool("codexautoai-nonexistent-binary-12345")
        assert result is None

    def test_return_type_is_str_or_none(self):
        result = resolve_tool("git")
        assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# mode3_authorized — SECGOV-R6 / C11
# ---------------------------------------------------------------------------


class TestMode3Authorized:
    # Injection / embedded path — NEVER authorizes

    def test_embedded_token_never_authorizes(self):
        """Prompt injection cannot self-authorize MODE3."""
        assert mode3_authorized("valid-token", embedded=True) is False

    def test_embedded_with_strong_token_still_false(self):
        assert mode3_authorized("super-secret-admin-key", embedded=True) is False

    def test_embedded_with_none_token_false(self):
        assert mode3_authorized(None, embedded=True) is False

    def test_embedded_with_empty_token_false(self):
        assert mode3_authorized("", embedded=True) is False

    # Out-of-band path — valid token authorizes

    def test_valid_oob_token_authorizes(self):
        assert mode3_authorized("valid-token", embedded=False) is True

    def test_valid_oob_token_any_string_authorizes(self):
        assert mode3_authorized("human-issued-token-abc123", embedded=False) is True

    # Out-of-band but missing/empty token — does NOT authorize

    def test_none_token_oob_is_false(self):
        assert mode3_authorized(None, embedded=False) is False

    def test_empty_string_token_oob_is_false(self):
        assert mode3_authorized("", embedded=False) is False

    # Default embedded parameter is False (positional usage)

    def test_default_embedded_false_valid_token(self):
        # mode3_authorized(token) without embedded kwarg defaults to embedded=False
        assert mode3_authorized("my-token") is True

    def test_default_embedded_false_none_token(self):
        assert mode3_authorized(None) is False
