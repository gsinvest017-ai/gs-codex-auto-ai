"""啟動期全域設定 overlay 測試（套用 / 還原 / 引用計數 / 殭屍清理）。

全部在 tmp_path 假 HOME 上操作——不碰使用者真正的 ~/.claude / ~/.codex。
跑法：python -m pytest tests/test_global_overlay.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# desktop/ 非 package，加進 sys.path 後 import
_DESKTOP = Path(__file__).resolve().parent.parent / "desktop"
if str(_DESKTOP) not in sys.path:
    sys.path.insert(0, str(_DESKTOP))

import global_overlay as go  # noqa: E402


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """把 Path.home() 指到 tmp_path，並停用任何 disable 旗標。"""
    monkeypatch.setattr(go.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CODEXAUTOAI_NO_GLOBAL_OVERLAY", raising=False)
    return tmp_path


def _read_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


# ── Claude permissions ───────────────────────────────────────────────────────
def test_claude_absent_apply_then_revert_deletes(home):
    p = go.claude_settings_path()
    assert not p.exists()
    backup = go._apply_claude()
    assert backup["file_absent"] is True
    assert _read_json(p)["permissions"]["defaultMode"] == "bypassPermissions"
    go._revert_claude(backup)
    assert not p.exists()  # 本來不存在 → 還原後刪掉


def test_claude_existing_no_permissions_preserves_other_keys(home):
    p = go.claude_settings_path()
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"env": {"X": "1"}, "hooks": {"Stop": []}}),
                 encoding="utf-8")
    backup = go._apply_claude()
    data = _read_json(p)
    assert data["permissions"]["defaultMode"] == "bypassPermissions"
    assert data["env"] == {"X": "1"} and data["hooks"] == {"Stop": []}
    go._revert_claude(backup)
    data = _read_json(p)
    assert "permissions" not in data           # 本來沒有 → 移除
    assert data["env"] == {"X": "1"} and data["hooks"] == {"Stop": []}


def test_claude_existing_permissions_restored_exactly(home):
    p = go.claude_settings_path()
    p.parent.mkdir(parents=True)
    original = {"permissions": {"defaultMode": "default", "allow": ["Read(*)"]},
                "env": {"A": "B"}}
    p.write_text(json.dumps(original), encoding="utf-8")
    backup = go._apply_claude()
    assert _read_json(p)["permissions"]["defaultMode"] == "bypassPermissions"
    go._revert_claude(backup)
    assert _read_json(p) == original           # 一字不差還原


# ── Codex config.toml ────────────────────────────────────────────────────────
def test_codex_absent_apply_then_revert_deletes(home):
    p = go.codex_config_path()
    assert not p.exists()
    backup = go._apply_codex()
    txt = p.read_text(encoding="utf-8")
    assert 'approval_policy = "on-failure"' in txt
    assert 'sandbox_mode = "workspace-write"' in txt
    go._revert_codex(backup)
    assert not p.exists()


def test_codex_preserves_sections_and_restores_top_level_dups(home):
    p = go.codex_config_path()
    p.parent.mkdir(parents=True)
    original = (
        'approval_policy = "untrusted"\n'
        'model = "gpt-5"\n'
        "\n"
        "[mcp_servers.foo]\n"
        'command = "x"\n'
        'sandbox_mode = "read-only"\n'   # 在 table 內 → 屬於該 table，不可動
    )
    p.write_text(original, encoding="utf-8")
    backup = go._apply_codex()
    txt = p.read_text(encoding="utf-8")
    # overlay 區塊在檔首頂層
    assert txt.startswith(go._CODEX_BEGIN)
    assert 'approval_policy = "on-failure"' in txt
    # 頂層原本的 approval_policy 被移除（不重複），但 table 內的 sandbox_mode 保留
    assert txt.count('approval_policy = "untrusted"') == 0
    assert 'model = "gpt-5"' in txt
    assert "[mcp_servers.foo]" in txt
    assert 'sandbox_mode = "read-only"' in txt          # table 內那行原封不動
    # 還原：區塊消失、頂層 dup 鍵復原
    go._revert_codex(backup)
    txt2 = p.read_text(encoding="utf-8")
    assert go._CODEX_BEGIN not in txt2
    assert 'approval_policy = "untrusted"' in txt2
    assert 'model = "gpt-5"' in txt2
    assert "[mcp_servers.foo]" in txt2
    assert 'sandbox_mode = "read-only"' in txt2


def test_codex_reapply_is_idempotent(home):
    p = go.codex_config_path()
    p.parent.mkdir(parents=True)
    p.write_text('model = "gpt-5"\n', encoding="utf-8")
    go._apply_codex()
    go._apply_codex()  # 重套不該疊加區塊
    txt = p.read_text(encoding="utf-8")
    assert txt.count(go._CODEX_BEGIN) == 1
    assert txt.count('approval_policy = "on-failure"') == 1


# ── acquire / release 引用計數 ───────────────────────────────────────────────
def test_refcount_last_owner_reverts(home):
    cp = go.claude_settings_path()
    cp.parent.mkdir(parents=True)
    cp.write_text(json.dumps({"env": {"A": "B"}}), encoding="utf-8")

    r1 = go.acquire("desktop:1")
    assert r1["applied"] is True
    r2 = go.acquire("vscode:2")
    assert r2["applied"] is False and r2["owners"] == 2
    assert _read_json(cp)["permissions"]["defaultMode"] == "bypassPermissions"

    rel1 = go.release("desktop:1")
    assert rel1["owners"] == 1 and rel1["reverted"] is False
    assert "permissions" in _read_json(cp)              # 還有 owner → 維持套用

    rel2 = go.release("vscode:2")
    assert rel2["owners"] == 0 and rel2["reverted"] is True
    assert "permissions" not in _read_json(cp)          # 最後一個走了 → 還原
    assert not go.state_path().exists()                 # state 清乾淨


def test_prune_removes_dead_owner(home, monkeypatch):
    cp = go.claude_settings_path()
    cp.parent.mkdir(parents=True)
    cp.write_text(json.dumps({"env": {"A": "B"}}), encoding="utf-8")

    # 第一個 owner 用「永遠存活」騙過 prune
    monkeypatch.setattr(go, "_pid_alive", lambda pid: pid == 111)
    monkeypatch.setattr(go.os, "getpid", lambda: 111)
    go.acquire("alive:111")
    # 偽造一個死 owner 進 state
    st = go._load_state()
    st["owners"]["dead:999"] = {"pid": 999, "ts": go.time.time()}
    go._save_state(st)

    # 新 acquire 會 prune 掉 dead:999
    monkeypatch.setattr(go.os, "getpid", lambda: 111)
    go.acquire("alive:111")
    assert "dead:999" not in go._load_state()["owners"]


def test_disabled_env_noops(home, monkeypatch):
    monkeypatch.setenv("CODEXAUTOAI_NO_GLOBAL_OVERLAY", "1")
    r = go.acquire("desktop:1")
    assert r["applied"] is False and r["reason"] == "disabled"
    assert not go.claude_settings_path().exists()
