"""桌面 App updater 測試（版本比對、release 檢查、guard）。

網路與 subprocess 全 monkeypatch — 不打真的 GitHub、不啟動安裝程式。
跑法：python -m pytest tests/test_updater.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# desktop/ 非 package，把它加進 sys.path 後 import updater
_DESKTOP = Path(__file__).resolve().parent.parent / "desktop"
if str(_DESKTOP) not in sys.path:
    sys.path.insert(0, str(_DESKTOP))

import updater  # noqa: E402


@pytest.fixture(autouse=True)
def _no_manifest(monkeypatch):
    """預設停用 raw manifest（避免測試打真網路）；測 manifest 的 test 自行覆寫。"""
    monkeypatch.setattr(updater, "_fetch_manifest", lambda: None)


# ---- version parsing / compare ---- #


@pytest.mark.parametrize("s,expected", [
    ("0.1.1", (0, 1, 1)),
    ("v0.1.1", (0, 1, 1)),
    ("app-v1.2.3", (1, 2, 3)),
    ("nope", None),
])
def test_parse_ver(s, expected):
    assert updater._parse_ver(s) == expected


@pytest.mark.parametrize("latest,current,expected", [
    ("0.1.2", "0.1.1", True),
    ("0.2.0", "0.1.9", True),
    ("1.0.0", "0.9.9", True),
    ("0.1.1", "0.1.1", False),
    ("0.1.0", "0.1.1", False),     # downgrade → not newer
    ("garbage", "0.1.1", False),
])
def test_is_newer(latest, current, expected):
    assert updater.is_newer(latest, current) is expected


# ---- local version ---- #


def test_local_version_reads_desktop_version(tmp_path, monkeypatch):
    (tmp_path / "desktop").mkdir()
    (tmp_path / "desktop" / "VERSION").write_text("9.8.7\n", encoding="utf-8")
    monkeypatch.setenv("CODEXAUTOAI_APP_DIR", str(tmp_path))
    assert updater.local_version() == "9.8.7"


def test_local_version_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEXAUTOAI_APP_DIR", str(tmp_path))
    assert updater.local_version() == "0.0.0"


# ---- asset selection ---- #


def test_pick_asset_picks_exe():
    assets = [{"name": "notes.txt", "id": 1},
              {"name": "CodexAutoAI-setup-0.1.1.exe", "id": 2}]
    assert updater._pick_asset(assets)["id"] == 2


def test_pick_asset_none():
    assert updater._pick_asset([{"name": "notes.txt", "id": 1}]) is None


# ---- token resolution ---- #


def test_gh_token_env(monkeypatch):
    monkeypatch.setenv("CODEXAUTOAI_GH_TOKEN", "tok-abc")
    assert updater.gh_token() == "tok-abc"


def test_gh_token_none(monkeypatch, tmp_path):
    for v in ("CODEXAUTOAI_GH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(updater.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(updater.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert updater.gh_token() is None


# ---- check_update ---- #


def _fake_releases():
    return [
        {"tag_name": "ext-v0.5.0", "draft": False, "assets": []},   # 別元件，須略過
        {"tag_name": "app-v0.2.0", "draft": False,
         "body": "release notes", "html_url": "http://x/app-v0.2.0",
         "assets": [{"name": "CodexAutoAI-setup-0.2.0.exe", "id": 43}]},
        {"tag_name": "app-v0.1.0", "draft": False, "assets": []},
    ]


def test_check_update_available(monkeypatch):
    monkeypatch.setattr(updater, "local_version", lambda: "0.1.1")
    monkeypatch.setattr(updater, "gh_token", lambda: "tok")
    monkeypatch.setattr(updater, "_api_get",
                        lambda path, token, timeout=8.0: _fake_releases())
    r = updater.check_update()
    assert r["update_available"] is True
    assert r["latest"] == "0.2.0" and r["tag"] == "app-v0.2.0"
    assert r["asset_name"] == "CodexAutoAI-setup-0.2.0.exe"
    assert r["auth_ok"] is True


def test_check_update_up_to_date(monkeypatch):
    monkeypatch.setattr(updater, "local_version", lambda: "0.2.0")
    monkeypatch.setattr(updater, "gh_token", lambda: "tok")
    monkeypatch.setattr(updater, "_api_get",
                        lambda path, token, timeout=8.0: _fake_releases())
    assert updater.check_update()["update_available"] is False


def test_check_update_no_auth(monkeypatch):
    monkeypatch.setattr(updater, "local_version", lambda: "0.1.1")
    monkeypatch.setattr(updater, "gh_token", lambda: None)
    monkeypatch.setattr(updater, "_api_get", lambda path, token, timeout=8.0: None)
    r = updater.check_update()
    assert r["auth_ok"] is False
    assert r["update_available"] is False
    assert r["error"] and "token" in r["error"]


# ---- apply guards ---- #


def test_apply_refuses_in_dev(monkeypatch):
    monkeypatch.setattr(updater, "is_installed", lambda: False)
    assert updater.apply_update()["status"] == "refused"


def test_apply_errors_when_no_auth(monkeypatch):
    monkeypatch.setattr(updater, "is_installed", lambda: True)
    monkeypatch.setattr(updater, "check_update",
                        lambda: {"auth_ok": False, "error": "no token"})
    assert updater.apply_update()["status"] == "error"


def test_apply_up_to_date(monkeypatch):
    monkeypatch.setattr(updater, "is_installed", lambda: True)
    monkeypatch.setattr(updater, "check_update",
                        lambda: {"auth_ok": True, "update_available": False,
                                 "current": "0.2.0"})
    assert updater.apply_update()["status"] == "up-to-date"


# ---- raw manifest 優先路徑 ---- #


def _fake_manifest(app_ver):
    return {"app": {"version": app_ver, "tag": f"app-v{app_ver}",
                    "exe": f"https://github.com/x/y/releases/download/app-v{app_ver}/CodexAutoAI-setup-{app_ver}.exe"}}


def test_manifest_path_new_version(monkeypatch):
    monkeypatch.setattr(updater, "local_version", lambda: "0.2.0")
    monkeypatch.setattr(updater, "_fetch_manifest", lambda: _fake_manifest("0.2.3"))
    # manifest 命中就不該碰 API：若被呼叫直接讓測試失敗
    monkeypatch.setattr(updater, "_latest_release",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不應走 API")))
    r = updater.check_update()
    assert r["auth_ok"] is True
    assert r["latest"] == "0.2.3" and r["update_available"] is True
    assert r["asset_name"] == "CodexAutoAI-setup-0.2.3.exe"
    assert r["asset_url"].endswith("CodexAutoAI-setup-0.2.3.exe")


def test_manifest_path_up_to_date(monkeypatch):
    monkeypatch.setattr(updater, "local_version", lambda: "0.2.3")
    monkeypatch.setattr(updater, "_fetch_manifest", lambda: _fake_manifest("0.2.3"))
    r = updater.check_update()
    assert r["update_available"] is False and r["latest"] == "0.2.3"


def test_apply_prefers_manifest_asset_url(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "is_installed", lambda: True)
    monkeypatch.setattr(updater, "check_update", lambda: {
        "auth_ok": True, "update_available": True, "current": "0.2.0",
        "latest": "0.2.3", "tag": "app-v0.2.3",
        "asset_name": "CodexAutoAI-setup-0.2.3.exe",
        "asset_url": "https://example/CodexAutoAI-setup-0.2.3.exe"})
    calls = {"url": None, "asset": False}

    def fake_url(url, dest):
        calls["url"] = url
        dest.write_bytes(b"x")
        return True
    monkeypatch.setattr(updater, "_download_url", fake_url)
    monkeypatch.setattr(updater, "_download_asset",
                        lambda *a, **k: calls.__setitem__("asset", True) or True)
    monkeypatch.setattr(updater.os, "startfile", lambda p: None, raising=False)
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: None)
    res = updater.apply_update()
    assert res["status"] == "updating"
    assert calls["url"] == "https://example/CodexAutoAI-setup-0.2.3.exe"
    assert calls["asset"] is False  # 直連成功就不該退回 gh/asset API
