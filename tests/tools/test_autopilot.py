"""autopilot arm/cont hook 測試（fake stdin，零 LLM）。

驗證五道安全閥 + per-session 隔離（A 的 done 不影響 B）。
"""
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


arm = _load("ap_arm", "tools/autopilot/arm.py")
cont = _load("ap_cont", "tools/autopilot/cont.py")


def _run(mod, payload, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    rc = mod.main()
    out = capsys.readouterr().out
    return rc, out


def test_cont_no_state_allows_stop(tmp_path, monkeypatch, capsys):
    rc, out = _run(cont, {"session_id": "s1"}, monkeypatch, tmp_path, capsys)
    assert rc == 0 and out.strip() == ""          # 沒開 autopilot → 放行


def test_arm_on_then_cont_blocks(tmp_path, monkeypatch, capsys):
    _run(arm, {"session_id": "s1", "prompt": "/autopilot on 做一個加法 CLI"}, monkeypatch, tmp_path, capsys)
    assert (tmp_path / "log" / "autopilot" / "s1.json").exists()
    rc, out = _run(cont, {"session_id": "s1"}, monkeypatch, tmp_path, capsys)
    assert rc == 0
    assert json.loads(out)["decision"] == "block"  # 開了 → 擋回去續跑


def test_done_sentinel_allows_stop(tmp_path, monkeypatch, capsys):
    _run(arm, {"session_id": "s1", "prompt": "/autopilot on x"}, monkeypatch, tmp_path, capsys)
    (tmp_path / "log" / "autopilot" / "s1.done").write_text("", encoding="utf-8")
    rc, out = _run(cont, {"session_id": "s1"}, monkeypatch, tmp_path, capsys)
    assert rc == 0 and out.strip() == ""           # done → 放行
    assert not (tmp_path / "log" / "autopilot" / "s1.json").exists()  # 清檔


def test_stop_hook_active_allows_stop(tmp_path, monkeypatch, capsys):
    _run(arm, {"session_id": "s1", "prompt": "/autopilot on x"}, monkeypatch, tmp_path, capsys)
    rc, out = _run(cont, {"session_id": "s1", "stop_hook_active": True}, monkeypatch, tmp_path, capsys)
    assert rc == 0 and out.strip() == ""           # 閥 1


def test_iteration_cap(tmp_path, monkeypatch, capsys):
    _run(arm, {"session_id": "s1", "prompt": "/autopilot on x"}, monkeypatch, tmp_path, capsys)
    sf = tmp_path / "log" / "autopilot" / "s1.json"
    st = json.loads(sf.read_text(encoding="utf-8")); st["iterations"] = st["max_iterations"]
    sf.write_text(json.dumps(st), encoding="utf-8")
    rc, out = _run(cont, {"session_id": "s1"}, monkeypatch, tmp_path, capsys)
    assert rc == 0 and out.strip() == ""           # 達上限 → 停
    assert not sf.exists()


def test_per_session_isolation(tmp_path, monkeypatch, capsys):
    _run(arm, {"session_id": "A", "prompt": "/autopilot on a"}, monkeypatch, tmp_path, capsys)
    _run(arm, {"session_id": "B", "prompt": "/autopilot on b"}, monkeypatch, tmp_path, capsys)
    # A 完成
    (tmp_path / "log" / "autopilot" / "A.done").write_text("", encoding="utf-8")
    rc_a, out_a = _run(cont, {"session_id": "A"}, monkeypatch, tmp_path, capsys)
    rc_b, out_b = _run(cont, {"session_id": "B"}, monkeypatch, tmp_path, capsys)
    assert out_a.strip() == ""                      # A 放行
    assert json.loads(out_b)["decision"] == "block" # B 不受影響，仍續跑


def test_off_clears_state(tmp_path, monkeypatch, capsys):
    _run(arm, {"session_id": "s1", "prompt": "/autopilot on x"}, monkeypatch, tmp_path, capsys)
    _run(arm, {"session_id": "s1", "prompt": "/autopilot off"}, monkeypatch, tmp_path, capsys)
    assert not (tmp_path / "log" / "autopilot" / "s1.json").exists()
