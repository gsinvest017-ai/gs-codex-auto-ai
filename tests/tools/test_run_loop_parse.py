"""Stage 2 — run_loop.py 解析器純函式測試。"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("run_loop", ROOT / "tools/run_loop.py")
run_loop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_loop)


def test_pytest_pass_returns_empty():
    assert run_loop.parse_pytest_failures("3 passed", "", 0) == []


def test_pytest_two_failures_sorted_unique():
    out = "FAILED tests/t.py::b\nFAILED tests/t.py::a\nFAILED tests/t.py::a\n"
    assert run_loop.parse_pytest_failures(out, "", 1) == ["tests/t.py::a", "tests/t.py::b"]


def test_pytest_error_line_counts():
    assert run_loop.parse_pytest_failures("ERROR tests/t.py::setup", "", 1) == ["tests/t.py::setup"]


def test_pytest_crash_without_parseable_failures_is_a_defect():
    # 非零 exit 但解析不到具體失敗 → 不可假裝通過
    assert run_loop.parse_pytest_failures("Traceback...", "boom", 2) == ["pytest:unknown-failure"]


def test_pytest_no_tests_collected_is_a_defect():
    assert run_loop.parse_pytest_failures("no tests ran", "", 5) == ["pytest:no-tests"]


def test_issue_list_closed_vocabulary(tmp_path):
    f = tmp_path / "out.txt"
    f.write_text("MISSING:FN-003\nEXTRA:FN-009\nMISMATCH:FN-003\nnoise line\nMISSING:FN-003\n",
                 encoding="utf-8")
    assert run_loop.parse_issue_list(str(f)) == ["EXTRA:FN-009", "MISMATCH:FN-003", "MISSING:FN-003"]
