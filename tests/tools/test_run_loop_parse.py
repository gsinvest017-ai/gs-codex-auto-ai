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


# ── 結構化 review 契約（M2）────────────────────────────────────────────────
class TestStructuredReview:
    """舊做法是 regex 從自由文字刮 TYPE:ID，於是「看完覺得沒問題」與「根本沒產出」
    都是空清單——語意相反卻分不出來，實測造成 Phase 4 連兩次假通過。

    有了明確的 verdict，「通過」是 reviewer **說出來的**，不是從「找不到東西」推論的。
    """

    PASS = '{"verdict": "pass", "findings": []}'
    FAIL = ('{"verdict": "changes_requested", "findings": ['
            '{"type": "MISSING", "id": "FN-3", "detail": "沒有錯誤處理"},'
            '{"type": "MISMATCH", "id": "FN-7", "detail": "簽名不符"}]}')

    def test_pass_verdict_yields_no_defects(self, tmp_path):
        f = tmp_path / "r.json"
        f.write_text(self.PASS, encoding="utf-8")
        assert run_loop.parse_issue_list(str(f)) == []
        assert run_loop.review_said_pass(str(f)) is True

    def test_findings_become_stable_ids(self, tmp_path):
        f = tmp_path / "r.json"
        f.write_text(self.FAIL, encoding="utf-8")
        assert run_loop.parse_issue_list(str(f)) == ["MISMATCH:FN-7", "MISSING:FN-3"]
        assert run_loop.review_said_pass(str(f)) is False

    def test_empty_output_is_not_a_pass(self, tmp_path):
        """關鍵區別：沒產出 ≠ 通過。這正是假通過的來源。"""
        f = tmp_path / "r.json"
        f.write_text("", encoding="utf-8")
        assert run_loop.review_said_pass(str(f)) is False
        assert run_loop.parse_structured_review("") == (False, [])

    def test_prose_around_the_json_is_tolerated(self):
        """Codex 很常在 JSON 前後多寫幾句話——不能因此整包讀不到。"""
        raw = '我看完了，結論如下：\n{"verdict":"pass","findings":[]}\n以上。'
        ok, ids = run_loop.parse_structured_review(raw)
        assert ok is True and ids == []

    def test_json_without_verdict_is_not_structured(self):
        """少了 verdict 就退回舊路徑——那是這整個契約的重點欄位。"""
        ok, _ = run_loop.parse_structured_review('{"findings": [{"type":"MISSING","id":"FN-1"}]}')
        assert ok is False

    def test_falls_back_to_legacy_text_format(self, tmp_path):
        """舊的 reviewer prompt 產出純文字，換契約不能把既有流程弄壞。"""
        f = tmp_path / "r.txt"
        f.write_text("MISSING:FN-1\nMISMATCH:FN-2\n", encoding="utf-8")
        assert run_loop.parse_issue_list(str(f)) == ["MISMATCH:FN-2", "MISSING:FN-1"]

    def test_unknown_finding_types_are_dropped(self):
        """封閉詞彙——不然 no-progress 的 hash 會被亂七八糟的型別汙染。"""
        ok, ids = run_loop.parse_structured_review(
            '{"verdict":"changes_requested","findings":['
            '{"type":"NITPICK","id":"X"},{"type":"MISSING","id":"FN-1"}]}')
        assert ok is True and ids == ["MISSING:FN-1"]

    def test_malformed_json_does_not_crash(self, tmp_path):
        f = tmp_path / "r.json"
        f.write_text('{"verdict": "pass", findings: [}', encoding="utf-8")
        assert run_loop.parse_issue_list(str(f)) == []
        assert run_loop.review_said_pass(str(f)) is False
