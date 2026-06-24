"""
test_syntax_guard.py — Tests for src/codexautoai_v2/syntax_guard.py

Covers BUILD-R5 / SWE-agent ACI lint-guard requirements:
  - valid Python  -> ok True
  - invalid Python -> ok False, line set, error present, context includes bad line
  - valid JSON    -> ok True
  - invalid JSON  -> ok False, line set, error present
  - check() dispatches by extension; unknown extension -> ok True
  - guard_write raises SyntaxGuardError on bad Python; exception carries GuardResult
"""

import pytest

from src.codexautoai_v2.syntax_guard import (
    GuardResult,
    SyntaxGuardError,
    check,
    check_json,
    check_python,
    guard_write,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_PYTHON = """\
def add(a, b):
    return a + b

class Foo:
    pass
"""

# Missing closing parenthesis in function signature — guaranteed SyntaxError
INVALID_PYTHON = """\
def f(:
    pass
"""

VALID_JSON = '{"key": "value", "num": 42, "arr": [1, 2, 3]}'

INVALID_JSON = '{"key": "value", "broken":'


# ---------------------------------------------------------------------------
# check_python — valid
# ---------------------------------------------------------------------------


class TestCheckPythonValid:
    def test_returns_guard_result(self):
        result = check_python(VALID_PYTHON)
        assert isinstance(result, GuardResult)

    def test_ok_is_true(self):
        result = check_python(VALID_PYTHON)
        assert result.ok is True

    def test_no_error(self):
        result = check_python(VALID_PYTHON)
        assert result.error is None

    def test_no_line(self):
        result = check_python(VALID_PYTHON)
        assert result.line is None

    def test_no_col(self):
        result = check_python(VALID_PYTHON)
        assert result.col is None

    def test_no_context(self):
        result = check_python(VALID_PYTHON)
        assert result.context is None

    def test_empty_source(self):
        result = check_python("")
        assert result.ok is True

    def test_single_expression(self):
        result = check_python("x = 1 + 2\n")
        assert result.ok is True


# ---------------------------------------------------------------------------
# check_python — invalid
# ---------------------------------------------------------------------------


class TestCheckPythonInvalid:
    def setup_method(self):
        self.result = check_python(INVALID_PYTHON)

    def test_ok_is_false(self):
        assert self.result.ok is False

    def test_line_is_set(self):
        assert self.result.line is not None
        assert isinstance(self.result.line, int)
        assert self.result.line >= 1

    def test_error_is_present(self):
        assert self.result.error is not None
        assert isinstance(self.result.error, str)
        assert len(self.result.error) > 0

    def test_context_is_present(self):
        assert self.result.context is not None

    def test_context_contains_bad_line(self):
        # The bad line "def f(:" should appear in the context snippet
        assert "def f(" in self.result.context

    def test_context_has_line_marker(self):
        # Context lines use ">" marker on the error line
        assert ">" in self.result.context

    def test_col_is_set_or_none(self):
        # col may or may not be set depending on CPython version, but if set it's int
        if self.result.col is not None:
            assert isinstance(self.result.col, int)

    def test_different_error_line_numbers(self):
        # Error on line 3
        src = "a = 1\nb = 2\ndef bad(:\n    pass\n"
        r = check_python(src)
        assert r.ok is False
        assert r.line == 3

    def test_context_shows_surrounding_lines(self):
        # Error on line 3 of a 5-line snippet — context should include line 2 and 3
        src = "x = 1\ny = 2\ndef bad(:\n    pass\nz = 3\n"
        r = check_python(src)
        assert r.ok is False
        assert "y = 2" in r.context  # line before error
        assert "def bad(" in r.context  # the error line


# ---------------------------------------------------------------------------
# check_json — valid
# ---------------------------------------------------------------------------


class TestCheckJsonValid:
    def test_ok_is_true(self):
        result = check_json(VALID_JSON)
        assert result.ok is True

    def test_no_error(self):
        result = check_json(VALID_JSON)
        assert result.error is None

    def test_empty_object(self):
        result = check_json("{}")
        assert result.ok is True

    def test_array(self):
        result = check_json("[1, 2, 3]")
        assert result.ok is True

    def test_nested(self):
        result = check_json('{"a": {"b": [1, null, true]}}')
        assert result.ok is True


# ---------------------------------------------------------------------------
# check_json — invalid
# ---------------------------------------------------------------------------


class TestCheckJsonInvalid:
    def setup_method(self):
        self.result = check_json(INVALID_JSON)

    def test_ok_is_false(self):
        assert self.result.ok is False

    def test_error_present(self):
        assert self.result.error is not None
        assert len(self.result.error) > 0

    def test_line_set(self):
        assert self.result.line is not None
        assert isinstance(self.result.line, int)

    def test_col_set(self):
        assert self.result.col is not None
        assert isinstance(self.result.col, int)

    def test_context_present(self):
        assert self.result.context is not None

    def test_trailing_comma(self):
        r = check_json('{"a": 1,}')
        assert r.ok is False

    def test_unquoted_key(self):
        r = check_json("{key: 1}")
        assert r.ok is False


# ---------------------------------------------------------------------------
# check() — dispatch by extension
# ---------------------------------------------------------------------------


class TestCheckDispatch:
    def test_dot_py_dispatches_to_python_valid(self):
        r = check("module.py", VALID_PYTHON)
        assert r.ok is True

    def test_dot_py_dispatches_to_python_invalid(self):
        r = check("module.py", INVALID_PYTHON)
        assert r.ok is False
        assert r.line is not None

    def test_dot_json_dispatches_to_json_valid(self):
        r = check("config.json", VALID_JSON)
        assert r.ok is True

    def test_dot_json_dispatches_to_json_invalid(self):
        r = check("config.json", INVALID_JSON)
        assert r.ok is False

    def test_unknown_extension_ok_true(self):
        r = check("readme.md", "# Hello")
        assert r.ok is True

    def test_unknown_extension_no_extension(self):
        r = check("Makefile", "all:\n\techo hi")
        assert r.ok is True

    def test_unknown_extension_carries_note(self):
        r = check("script.sh", "#!/bin/bash\necho hi")
        assert r.ok is True
        # The module documents that a note may be present in error field
        # when ok is True and the extension is unknown — that's acceptable.

    def test_full_path_windows_style(self):
        r = check(r"C:\Users\user\project\src\app.py", VALID_PYTHON)
        assert r.ok is True

    def test_full_path_posix_style(self):
        r = check("/home/user/project/src/app.py", VALID_PYTHON)
        assert r.ok is True

    def test_uppercase_extension_normalised(self):
        # .PY should be treated same as .py
        r = check("module.PY", VALID_PYTHON)
        assert r.ok is True

    def test_dot_json_case_insensitive(self):
        r = check("data.JSON", VALID_JSON)
        assert r.ok is True


# ---------------------------------------------------------------------------
# guard_write — happy path
# ---------------------------------------------------------------------------


class TestGuardWriteValid:
    def test_valid_python_no_raise(self):
        guard_write("app.py", VALID_PYTHON)  # must not raise

    def test_valid_json_no_raise(self):
        guard_write("config.json", VALID_JSON)  # must not raise

    def test_unknown_extension_no_raise(self):
        guard_write("notes.txt", "hello world")  # must not raise

    def test_returns_none(self):
        result = guard_write("app.py", VALID_PYTHON)
        assert result is None


# ---------------------------------------------------------------------------
# guard_write — raises SyntaxGuardError on bad Python
# ---------------------------------------------------------------------------


class TestGuardWriteInvalidPython:
    def test_raises_syntax_guard_error(self):
        with pytest.raises(SyntaxGuardError):
            guard_write("broken.py", INVALID_PYTHON)

    def test_exception_carries_guard_result(self):
        with pytest.raises(SyntaxGuardError) as exc_info:
            guard_write("broken.py", INVALID_PYTHON)
        assert hasattr(exc_info.value, "result")
        assert isinstance(exc_info.value.result, GuardResult)

    def test_exception_result_ok_false(self):
        with pytest.raises(SyntaxGuardError) as exc_info:
            guard_write("broken.py", INVALID_PYTHON)
        assert exc_info.value.result.ok is False

    def test_exception_result_has_line(self):
        with pytest.raises(SyntaxGuardError) as exc_info:
            guard_write("broken.py", INVALID_PYTHON)
        assert exc_info.value.result.line is not None

    def test_exception_result_has_error_message(self):
        with pytest.raises(SyntaxGuardError) as exc_info:
            guard_write("broken.py", INVALID_PYTHON)
        assert exc_info.value.result.error is not None
        assert len(exc_info.value.result.error) > 0

    def test_exception_result_has_context(self):
        with pytest.raises(SyntaxGuardError) as exc_info:
            guard_write("broken.py", INVALID_PYTHON)
        assert exc_info.value.result.context is not None

    def test_exception_str_contains_error_info(self):
        with pytest.raises(SyntaxGuardError) as exc_info:
            guard_write("broken.py", INVALID_PYTHON)
        msg = str(exc_info.value)
        assert "line" in msg.lower() or "syntax" in msg.lower()


# ---------------------------------------------------------------------------
# guard_write — raises SyntaxGuardError on bad JSON
# ---------------------------------------------------------------------------


class TestGuardWriteInvalidJson:
    def test_raises_syntax_guard_error(self):
        with pytest.raises(SyntaxGuardError):
            guard_write("data.json", INVALID_JSON)

    def test_exception_carries_result(self):
        with pytest.raises(SyntaxGuardError) as exc_info:
            guard_write("data.json", INVALID_JSON)
        assert exc_info.value.result.ok is False
        assert exc_info.value.result.line is not None


# ---------------------------------------------------------------------------
# SyntaxGuardError — exception interface
# ---------------------------------------------------------------------------


class TestSyntaxGuardErrorInterface:
    def test_is_exception(self):
        result = GuardResult(ok=False, error="bad", line=1, col=1, context="1> bad")
        err = SyntaxGuardError(result)
        assert isinstance(err, Exception)

    def test_result_attribute(self):
        result = GuardResult(ok=False, error="bad", line=5, col=3, context="5> bad")
        err = SyntaxGuardError(result)
        assert err.result is result

    def test_str_representation(self):
        result = GuardResult(ok=False, error="invalid syntax", line=2, col=4)
        err = SyntaxGuardError(result)
        assert "invalid syntax" in str(err)
        assert "2" in str(err)
