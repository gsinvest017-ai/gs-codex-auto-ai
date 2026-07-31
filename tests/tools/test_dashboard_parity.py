"""events_model.py（Python）與 dashboard.js（JS）的事件解析必須產出相同結果。

為什麼需要這道測試：`log/events.jsonl` 目前有**兩個**解析實作——
`tools/events_model.py`（終端機 progress + desktop 進度卡）與
`vscode-extension/dashboard.js` 的 `summarizeEvents`（VS Code 控制台）。
兩邊若漂移，同一條 pipeline 在 App 與 IDE 會顯示不同的進度／分工證據，
而且沒人會立刻發現。

理想解是控制台直接吃 `python tools/events_model.py --json`，但 dashboard.js 是
0.10.0 的主要 UI、經過多輪修正且本身沒有測試，在合併期改它的資料層風險過高。
所以改成**機械化證明兩者等價**：任何一邊改了語意，這裡立刻紅。
遷移完成後可以刪掉本檔。
"""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "vscode-extension" / "dashboard.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not DASHBOARD.exists(),
    reason="需要 node 與 dashboard.js 才能比對",
)


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


em = _load("events_model", "tools/events_model.py")


# 涵蓋：正常推進、修復迴圈、分工證據、缺 codex 警示、跨 run、壞行
SCENARIOS = {
    "正常推進到 phase5": [
        {"event_type": "phase_start", "phase": "phase0"},
        {"event_type": "phase_end", "phase": "phase0", "status": "success"},
        {"event_type": "phase_start", "phase": "phase5"},
        {"event_type": "tool_call", "phase": "phase5", "tool": "codex exec --full-auto"},
        {"event_type": "llm_call", "gen_ai.usage.input_tokens": 1200,
         "gen_ai.usage.output_tokens": 300},
    ],
    "進了 phase5 但沒 codex（該警示）": [
        {"event_type": "phase_start", "phase": "phase5"},
        {"event_type": "llm_call", "gen_ai.usage.input_tokens": 900,
         "gen_ai.usage.output_tokens": 400},
    ],
    "phase5 完成且有 codex": [
        {"event_type": "phase_start", "phase": "phase5"},
        {"event_type": "tool_call", "phase": "phase5", "tool": "CODEX"},
        {"event_type": "phase_end", "phase": "phase5", "status": "success"},
    ],
    "codex 呼叫沒帶 phase（靠 cur_phase 推）": [
        {"event_type": "phase_start", "phase": "phase5"},
        {"event_type": "tool_call", "tool": "codex_runner"},
    ],
    "修復迴圈 tick 與成本": [
        {"event_type": "phase_start", "phase": "phase6"},
        {"event_type": "loop_tick", "phase": "phase6", "iteration": 2,
         "cumulative_cost_usd": 0.4213},
        {"event_type": "error", "phase": "phase6", "reason": "no_progress"},
    ],
    "非 codex 的 tool_call 不算": [
        {"event_type": "phase_start", "phase": "phase5"},
        {"event_type": "tool_call", "phase": "phase5", "tool": "syntax_gate"},
    ],
    "phase_end 失敗": [
        {"event_type": "phase_start", "phase": "phase3"},
        {"event_type": "phase_end", "phase": "phase3", "status": "failure"},
    ],
    "llm_call 缺 token 欄位": [
        {"event_type": "phase_start", "phase": "phase2"},
        {"event_type": "llm_call"},
    ],
    "空事件": [],
}

JS_DRIVER = r"""
const path = require("path");
const dash = require(process.argv[2]);
const scenarios = JSON.parse(process.argv[3]);
const out = {};
for (const [name, events] of Object.entries(scenarios)) {
  const lines = events.map((e) => JSON.stringify(e));
  out[name] = dash.summarizeEvents(lines);
}
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def js_results(tmp_path_factory):
    d = tmp_path_factory.mktemp("parity")
    driver = d / "driver.js"
    driver.write_text(JS_DRIVER, encoding="utf-8")
    proc = subprocess.run(
        [shutil.which("node"), str(driver), DASHBOARD.as_posix(),
         json.dumps(SCENARIOS, ensure_ascii=False)],
        capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, f"node 執行失敗：{proc.stderr}"
    return json.loads(proc.stdout)


def _py_view(events: list[dict]) -> dict:
    """把 Python 模型攤成 dashboard.js 的欄位形狀，方便逐欄比對。"""
    s = em.summarize(events)
    d = em.division_stats(events)
    return {
        "completed": sorted(s["completed"]),
        "iteration": s["iteration"],
        "cost": s["cost"],
        "failed": s["failed"],
        "claude": {"calls": d["claude"]["calls"],
                   "inTok": d["claude"]["in_tok"],
                   "outTok": d["claude"]["out_tok"]},
        "codex": {"calls": d["codex"]["calls"]},
        "codexInPhase5": d["codex_in_phase5"],
        "divisionWarning": d["division_warning"],
    }


def _js_view(raw: dict) -> dict:
    return {
        "completed": raw["completed"],
        "iteration": raw["iteration"],
        "cost": raw["cost"],
        "failed": raw["failed"],
        "claude": {"calls": raw["claude"]["calls"],
                   "inTok": raw["claude"]["inTok"],
                   "outTok": raw["claude"]["outTok"]},
        "codex": {"calls": raw["codex"]["calls"]},
        "codexInPhase5": raw["codexInPhase5"],
        "divisionWarning": raw["divisionWarning"],
    }


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_python_and_js_parsers_agree(name, js_results):
    py = _py_view(SCENARIOS[name])
    js = _js_view(js_results[name])
    assert py == js, f"「{name}」兩邊解析不一致：\npython={py}\njs    ={js}"


def test_division_warning_actually_fires():
    """別讓 parity 在「兩邊都壞」時假性通過——確認警示真的會亮。"""
    d = em.division_stats(SCENARIOS["進了 phase5 但沒 codex（該警示）"])
    assert d["division_warning"] is True
    ok = em.division_stats(SCENARIOS["正常推進到 phase5"])
    assert ok["division_warning"] is False
