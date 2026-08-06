"""
Tests for src/codexautoai_v2/property_verifier.py

Covers:
- parse_scenarios: 2-scenario markdown block -> 2 Property objects with correct bullets
- verify: pass / fail (falsy) / fail (exception) / skipped / ok logic
- stub_for: generated string contains 'def test_' and GIVEN/WHEN/THEN text
"""

from src.codexautoai_v2.property_verifier import (
    Property,
    VerificationReport,
    parse_scenarios,
    stub_for,
    verify,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

MARKDOWN_TWO_SCENARIOS = """
# Some spec

### Requirement: ORCH-R2 — loop termination
THE SYSTEM SHALL terminate after max_iterations.

#### Scenario: ORCH-R2-S1 — loop stops on max iterations
- GIVEN max_iterations = 3
- WHEN the 3rd fix still fails
- THEN the loop stops and escalates

#### Scenario: ORCH-R2-S2 — success exits early
- GIVEN max_iterations = 5
- WHEN the 2nd fix succeeds
- THEN the loop exits immediately with success status
"""

MARKDOWN_MULTILINE_BULLETS = """
#### Scenario: FOO-S1 — multi bullet
- GIVEN condition one
- GIVEN condition two
- WHEN event alpha
- WHEN event beta
- THEN outcome x
- THEN outcome y
"""


# ---------------------------------------------------------------------------
# parse_scenarios tests
# ---------------------------------------------------------------------------

class TestParseScenarios:
    def test_two_scenarios_count(self):
        props = parse_scenarios(MARKDOWN_TWO_SCENARIOS)
        assert len(props) == 2

    def test_first_scenario_id(self):
        props = parse_scenarios(MARKDOWN_TWO_SCENARIOS)
        assert props[0].scenario_id == "ORCH-R2-S1"

    def test_second_scenario_id(self):
        props = parse_scenarios(MARKDOWN_TWO_SCENARIOS)
        assert props[1].scenario_id == "ORCH-R2-S2"

    def test_first_given(self):
        props = parse_scenarios(MARKDOWN_TWO_SCENARIOS)
        assert props[0].given == ["max_iterations = 3"]

    def test_first_when(self):
        props = parse_scenarios(MARKDOWN_TWO_SCENARIOS)
        assert props[0].when == ["the 3rd fix still fails"]

    def test_first_then(self):
        props = parse_scenarios(MARKDOWN_TWO_SCENARIOS)
        assert props[0].then == ["the loop stops and escalates"]

    def test_second_given(self):
        props = parse_scenarios(MARKDOWN_TWO_SCENARIOS)
        assert props[1].given == ["max_iterations = 5"]

    def test_second_when(self):
        props = parse_scenarios(MARKDOWN_TWO_SCENARIOS)
        assert props[1].when == ["the 2nd fix succeeds"]

    def test_second_then(self):
        props = parse_scenarios(MARKDOWN_TWO_SCENARIOS)
        assert props[1].then == ["the loop exits immediately with success status"]

    def test_returns_property_instances(self):
        props = parse_scenarios(MARKDOWN_TWO_SCENARIOS)
        for p in props:
            assert isinstance(p, Property)

    def test_multi_bullets_given(self):
        props = parse_scenarios(MARKDOWN_MULTILINE_BULLETS)
        assert props[0].given == ["condition one", "condition two"]

    def test_multi_bullets_when(self):
        props = parse_scenarios(MARKDOWN_MULTILINE_BULLETS)
        assert props[0].when == ["event alpha", "event beta"]

    def test_multi_bullets_then(self):
        props = parse_scenarios(MARKDOWN_MULTILINE_BULLETS)
        assert props[0].then == ["outcome x", "outcome y"]

    def test_empty_markdown(self):
        assert parse_scenarios("") == []

    def test_no_bullets(self):
        md = "#### Scenario: BARE-S1 — no bullets\nSome prose only."
        props = parse_scenarios(md)
        assert len(props) == 1
        assert props[0].given == []
        assert props[0].when == []
        assert props[0].then == []


# ---------------------------------------------------------------------------
# verify tests
# ---------------------------------------------------------------------------

class TestVerify:
    def _props(self, *ids: str) -> list[Property]:
        return [Property(scenario_id=sid, given=[], when=[], then=[]) for sid in ids]

    def test_check_returns_true_is_passed(self):
        props = self._props("S1")
        report = verify(props, {"S1": lambda: True})
        assert "S1" in report.passed
        assert report.failed == []
        assert report.skipped == []

    def test_check_returns_false_is_failed(self):
        props = self._props("S1")
        report = verify(props, {"S1": lambda: False})
        assert "S1" in report.failed
        assert report.passed == []

    def test_check_raises_is_failed(self):
        def boom():
            raise RuntimeError("broken")

        props = self._props("S1")
        report = verify(props, {"S1": boom})
        assert "S1" in report.failed

    def test_missing_check_is_skipped(self):
        props = self._props("S1")
        report = verify(props, {})
        assert "S1" in report.skipped
        assert report.passed == []
        assert report.failed == []

    def test_ok_true_when_all_pass(self):
        props = self._props("S1", "S2")
        report = verify(props, {"S1": lambda: True, "S2": lambda: 1})
        assert report.ok is True

    def test_ok_false_when_any_failed(self):
        props = self._props("S1", "S2")
        report = verify(props, {"S1": lambda: True, "S2": lambda: False})
        assert report.ok is False

    def test_ok_false_when_any_skipped(self):
        props = self._props("S1", "S2")
        report = verify(props, {"S1": lambda: True})  # S2 has no check
        assert report.ok is False

    def test_ok_false_when_failed_and_skipped(self):
        props = self._props("S1", "S2", "S3")
        report = verify(props, {"S1": lambda: False})
        assert report.ok is False
        assert "S2" in report.skipped
        assert "S3" in report.skipped

    def test_empty_properties_ok(self):
        report = verify([], {})
        assert report.ok is True
        assert report.passed == []
        assert report.failed == []
        assert report.skipped == []

    def test_multiple_pass_fail_skip(self):
        props = self._props("P1", "F1", "SK1")
        report = verify(
            props,
            {
                "P1": lambda: True,
                "F1": lambda: None,  # falsy
            },
        )
        assert "P1" in report.passed
        assert "F1" in report.failed
        assert "SK1" in report.skipped
        assert report.ok is False

    def test_report_is_verificationreport_instance(self):
        report = verify([], {})
        assert isinstance(report, VerificationReport)


# ---------------------------------------------------------------------------
# stub_for tests
# ---------------------------------------------------------------------------

class TestStubFor:
    def _make_prop(self, sid: str) -> Property:
        return Property(
            scenario_id=sid,
            given=["max_iterations = 3"],
            when=["the 3rd fix still fails"],
            then=["the loop stops and escalates"],
        )

    def test_contains_def_test(self):
        prop = self._make_prop("ORCH-R2-S1")
        src = stub_for(prop)
        assert "def test_" in src

    def test_function_name_sanitized(self):
        prop = self._make_prop("ORCH-R2-S1")
        src = stub_for(prop)
        assert "def test_ORCH_R2_S1" in src

    def test_given_text_present(self):
        prop = self._make_prop("ORCH-R2-S1")
        src = stub_for(prop)
        assert "max_iterations = 3" in src

    def test_when_text_present(self):
        prop = self._make_prop("ORCH-R2-S1")
        src = stub_for(prop)
        assert "the 3rd fix still fails" in src

    def test_then_text_present(self):
        prop = self._make_prop("ORCH-R2-S1")
        src = stub_for(prop)
        assert "the loop stops and escalates" in src

    def test_given_keyword_in_comment(self):
        prop = self._make_prop("X-S1")
        src = stub_for(prop)
        assert "# GIVEN" in src

    def test_when_keyword_in_comment(self):
        prop = self._make_prop("X-S1")
        src = stub_for(prop)
        assert "# WHEN" in src

    def test_then_keyword_in_comment(self):
        prop = self._make_prop("X-S1")
        src = stub_for(prop)
        assert "# THEN" in src

    def test_assert_false_todo(self):
        prop = self._make_prop("X-S1")
        src = stub_for(prop)
        assert "assert False" in src
        assert "TODO" in src

    def test_returns_string(self):
        prop = self._make_prop("X-S1")
        assert isinstance(stub_for(prop), str)

    def test_empty_bullets(self):
        prop = Property(scenario_id="BARE-S1", given=[], when=[], then=[])
        src = stub_for(prop)
        assert "def test_BARE_S1" in src
        assert "assert False" in src

    def test_stub_from_spec_review_scenario(self):
        """Integration: parse a scenario from the real spec and generate stub."""
        md = """
#### Scenario: REVIEW-R3-S1 — 程式碼違反規格被擋
- GIVEN 需求 WHEN 輸入為空 THE SYSTEM SHALL 回傳錯誤
- WHEN 生成程式碼對空輸入回傳 null 而非錯誤
- THEN 屬性驗證 SHALL 失敗並阻擋進入下一 phase
"""
        props = parse_scenarios(md)
        assert len(props) == 1
        src = stub_for(props[0])
        assert "def test_REVIEW_R3_S1" in src
        assert "# GIVEN" in src
        assert "# WHEN" in src
        assert "# THEN" in src


# ── 中文場景名撞名（流水線回報的框架 bug #5）────────────────────────────────
import re as _re  # noqa: E402

from src.codexautoai_v2.property_verifier import (  # noqa: E402
    safe_name,
    stubs_for,
    unique_names,
)


def test_cjk_scenario_names_do_not_collide():
    """中文場景名以前整段被 `[^A-Za-z0-9]` 吃掉，全都變同一個名字互相覆蓋。"""
    ids = ["情境：新增支出", "情境：刪除支出", "情境：查詢餘額"]
    names = [safe_name(i) for i in ids]
    assert len(set(names)) == 3, f"中文應該保留下來才不會撞名：{names}"
    # 光是「互不相同」還不夠——退回 hash 也能做到互不相同，但名字會變得
    # 完全讀不出是哪個場景。要求原文留在名字裡，才真的驗到不是走 fallback。
    for sid, name in zip(ids, names):
        assert sid[-2:] in name, f"場景「{sid}」的名字 {name} 認不出來源"


def test_unique_names_suffixes_true_duplicates():
    """就算兩個場景真的同名，也要靠後綴分開——靜默覆蓋是這個 bug 最貴的部分。"""
    assert unique_names(["A", "A", "A"]) == ["A", "A_2", "A_3"]


def test_generated_stubs_are_all_distinct_functions():
    """端到端：23 個中文場景要產出 23 個 def，一個都不能少。"""
    props = [Property(scenario_id=f"情境{i}：測試項目{i}", given=["x"],
                      when=["y"], then=["z"]) for i in range(23)]
    src = "\n".join(stubs_for(props))
    names = _re.findall(r"^def (test_\w+)\(", src, _re.MULTILINE | _re.UNICODE)
    assert len(names) == 23 and len(set(names)) == 23, f"只剩 {len(set(names))} 個唯一名字"
    compile(src, "<stubs>", "exec")  # 產出必須是合法 Python


def test_stub_name_falls_back_when_everything_is_stripped():
    out = safe_name("!!!///")
    assert out.startswith("s_") and out[2:].isalnum()


def test_suffix_does_not_collide_with_a_real_name():
    """合成的 `_2` 後綴自己也會撞：`["A", "A_2", "A"]` 下第三個會撞到第二個的真名。

    那等於把同一個 bug 搬到上一層——生成檔裡仍然是兩個同名 def 互相覆蓋。
    """
    got = unique_names(["A", "A_2", "A"])
    assert len(set(got)) == 3, f"後綴撞到真名了：{got}"
    assert got[:2] == ["A", "A_2"], "前兩個是真名，不該被動到"


def test_pathological_suffix_chain_still_unique():
    ids = ["S", "S_2", "S_3", "S", "S", "S_4"]
    got = unique_names(ids)
    assert len(set(got)) == len(ids), got
