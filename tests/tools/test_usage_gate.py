"""usage_gate.py — token 用量閘門測試。

重點在驗證「與 gs-harness 的三個刻意差異」確實成立：預設關閉、fail-open、
protect_hours 預設空。這三點若被改成 harness 的預設，autopilot 會在使用者
按下「非停模式」後直接不跑，是嚴重回歸。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ug = _load("usage_gate", "tools/usage_gate.py")


def _clear_env(monkeypatch):
    for k in ("CODEXAUTOAI_USAGE_GATE", "CODEXAUTOAI_USAGE_MAX_PCT",
              "CODEXAUTOAI_PROTECT_HOURS", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(k, raising=False)


# ── 刻意差異 1：預設關閉 ────────────────────────────────────────────────────
def test_disabled_by_default(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    cfg = ug.load_cfg(tmp_path)
    assert cfg["enabled"] is False
    allowed, why = ug.evaluate_gate(None, 10, cfg)
    assert allowed is True
    assert "未啟用" in why


# ── 刻意差異 2：fail-open ───────────────────────────────────────────────────
def test_enabled_but_no_usage_data_is_fail_open(monkeypatch, tmp_path):
    """gs-harness 是 fail-closed；這裡必須 fail-open，否則沒裝 ccusage 就不能用。"""
    _clear_env(monkeypatch)
    cfg = {**ug.DEFAULT_CFG, "enabled": True}
    allowed, why = ug.evaluate_gate(None, 3, cfg)
    assert allowed is True
    assert "fail-open" in why


# ── 刻意差異 3：protect_hours 預設空 ───────────────────────────────────────
def test_no_daytime_protection_by_default(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    cfg = ug.load_cfg(tmp_path)
    assert cfg["protect_hours"] == []
    # 中午 12 點也不該被擋（使用者是自己按下 autopilot 的）
    cfg["enabled"] = True
    allowed, _ = ug.evaluate_gate(ug.Usage(10, 1000), 12, cfg)
    assert allowed is True


# ── 門檻判定 ────────────────────────────────────────────────────────────────
def test_blocks_over_threshold():
    cfg = {"enabled": True, "max_block_pct": 60, "protect_hours": []}
    allowed, why = ug.evaluate_gate(ug.Usage(700, 1000), 3, cfg)
    assert allowed is False
    assert "70.0%" in why and "60%" in why


def test_allows_under_threshold():
    cfg = {"enabled": True, "max_block_pct": 60, "protect_hours": []}
    allowed, why = ug.evaluate_gate(ug.Usage(300, 1000), 3, cfg)
    assert allowed is True
    assert "放行" in why


def test_force_overrides_block():
    cfg = {"enabled": True, "max_block_pct": 60, "protect_hours": [9, 18]}
    allowed, why = ug.evaluate_gate(ug.Usage(999, 1000), 10, cfg, force=True)
    assert allowed is True
    assert "force" in why


def test_protect_hours_blocks_when_configured():
    cfg = {"enabled": True, "max_block_pct": 60, "protect_hours": [9, 18]}
    allowed, why = ug.evaluate_gate(None, 10, cfg)
    assert allowed is False
    assert "保護時段" in why
    # 邊界：hi 是開區間
    assert ug.evaluate_gate(None, 18, cfg)[0] is True
    assert ug.evaluate_gate(None, 9, cfg)[0] is False


def test_zero_peak_does_not_imply_full(monkeypatch):
    """峰值 0 不能推論成 100%（那會在無歷史資料時誤擋）。"""
    assert ug.Usage(0, 0).pct == 0.0
    cfg = {"enabled": True, "max_block_pct": 60, "protect_hours": []}
    assert ug.evaluate_gate(ug.Usage(0, 0), 3, cfg)[0] is True


# ── 設定合併 ────────────────────────────────────────────────────────────────
def test_env_enables_and_sets_threshold(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CODEXAUTOAI_USAGE_GATE", "1")
    monkeypatch.setenv("CODEXAUTOAI_USAGE_MAX_PCT", "40")
    cfg = ug.load_cfg(tmp_path)
    assert cfg["enabled"] is True
    assert cfg["max_block_pct"] == 40


def test_env_protect_hours_formats(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CODEXAUTOAI_PROTECT_HOURS", "9-18")
    assert ug.load_cfg(tmp_path)["protect_hours"] == [9, 18]
    monkeypatch.setenv("CODEXAUTOAI_PROTECT_HOURS", "8,20")
    assert ug.load_cfg(tmp_path)["protect_hours"] == [8, 20]
    monkeypatch.setenv("CODEXAUTOAI_PROTECT_HOURS", "garbage")
    assert ug.load_cfg(tmp_path)["protect_hours"] == []


def test_toml_config_is_read(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    (tmp_path / ug.CONFIG_NAME).write_text(
        "[usage_gate]\nenabled = true\nmax_block_pct = 55\nprotect_hours = [1, 2]\n",
        encoding="utf-8")
    cfg = ug.load_cfg(tmp_path)
    assert cfg["enabled"] is True
    assert cfg["max_block_pct"] == 55
    assert cfg["protect_hours"] == [1, 2]


def test_broken_toml_does_not_raise(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    (tmp_path / ug.CONFIG_NAME).write_text("this is not [valid toml", encoding="utf-8")
    cfg = ug.load_cfg(tmp_path)          # 壞設定檔不該讓 pipeline 掛掉
    assert cfg["enabled"] is False


def test_env_overrides_toml(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    (tmp_path / ug.CONFIG_NAME).write_text("[usage_gate]\nenabled = true\n", encoding="utf-8")
    monkeypatch.setenv("CODEXAUTOAI_USAGE_GATE", "0")
    assert ug.load_cfg(tmp_path)["enabled"] is False


# ── CLI ─────────────────────────────────────────────────────────────────────
def test_cli_exit_code_reflects_verdict(monkeypatch, capsys, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setattr(ug, "check", lambda force=False, root=None: (False, "測試擋下"))
    assert ug.main(["--json"]) == 1
    assert '"allowed": false' in capsys.readouterr().out
    monkeypatch.setattr(ug, "check", lambda force=False, root=None: (True, "測試放行"))
    assert ug.main([]) == 0
