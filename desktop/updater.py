"""桌面 App 版本檢查 / 自動更新（借鏡 autogo src/autogo_dash/updater.py）。

CodexAutoAI 桌面 App 以 per-user Inno Setup 安裝檔發佈（見 installer/）。
本模組讓已安裝的版本能注意到 GitHub Release 有新版，並自動下載安裝檔升級：

    check_update()  → 比對本機版號與最新 ``app-v*`` Release tag
    apply_update()  → 下載最新安裝檔（authenticated）並啟動它；Inno Setup
                      以相同 AppId 覆蓋安裝（per-user → 免 UAC），完成後重啟。

repo 為 **private**，所有 GitHub 呼叫都需 token。解析順序：
``CODEXAUTOAI_GH_TOKEN`` / ``GH_TOKEN`` / ``GITHUB_TOKEN`` 環境變數 →
``~/.config/codexautoai/gh-token`` token 檔 → ``gh auth token``（gh CLI 已登入）。
完全沒有 token 時，檢查會降級為「無法讀取 release」，UI 改顯示手動下載連結。
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# PUBLIC 發行鏡像 repo（原始碼私有於 gs-codex-auto-ai）：公開 repo 讀 releases 免 token，
# 任何使用者都能檢查更新；token 只在需要提高 API rate limit 時才用（非必需）。
DEFAULT_REPO = "gsinvest017-ai/gs-codex-auto-ai-releases"
TAG_PREFIX = "app-v"  # 桌面 App 的 release tag 命名（與 VS Code extension 的 ext-v* 區隔）
_VER_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def repo() -> str:
    return (os.environ.get("CODEXAUTOAI_UPDATE_REPO") or DEFAULT_REPO).strip()


def app_dir() -> Path:
    """框架檔所在目錄（與 launcher.app_dir 一致）。

    凍結（PyInstaller）時 exe 與框架檔同目錄；未凍結時取 repo 根（desktop/ 的 parent）。
    可用 ``CODEXAUTOAI_APP_DIR`` 覆寫（測試用）。
    """
    env = os.environ.get("CODEXAUTOAI_APP_DIR")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


# ---- version ---- #


def _parse_ver(s: str) -> Optional[Tuple[int, int, int]]:
    m = _VER_RE.search(s or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def local_version() -> str:
    """讀本機 App 版號。優先 desktop/VERSION，其次 VERSION，最後 '0.0.0'。

    安裝版（凍結）由 build-installer 把 desktop/VERSION 一併打包到 {app}/desktop/；
    開發版則直接讀 repo 的 desktop/VERSION。
    """
    base = app_dir()
    for cand in (base / "desktop" / "VERSION", base / "VERSION"):
        try:
            txt = cand.read_text(encoding="utf-8").strip()
            if txt:
                return txt.splitlines()[0].strip()
        except OSError:
            continue
    return "0.0.0"


def is_newer(latest: str, current: str) -> bool:
    lt, cur = _parse_ver(latest), _parse_ver(current)
    if lt is None or cur is None:
        return False
    return lt > cur


# ---- auth ---- #


def gh_token() -> Optional[str]:
    for env in ("CODEXAUTOAI_GH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        v = (os.environ.get(env) or "").strip()
        if v:
            return v
    tok_file = Path.home() / ".config" / "codexautoai" / "gh-token"
    try:
        if tok_file.exists():
            t = tok_file.read_text(encoding="utf-8").strip()
            if t:
                return t
    except OSError:
        pass
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True,
                             text=True, timeout=6)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


# ---- install-mode guard ---- #


def is_installed() -> bool:
    """True 表示是真正的安裝版（凍結 exe 且非 git checkout）。

    開發 checkout（從 repo 直接跑 launcher.py）絕不該自我安裝 —
    apply 會在那裡拒絕；check 仍會回報版號。
    """
    return bool(getattr(sys, "frozen", False)) and not (app_dir() / ".git").exists()


# ---- release query ---- #


def _api_get(path: str, token: Optional[str], timeout: float = 8.0):
    url = f"https://api.github.com{path}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "codexautoai-updater")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        logger.info("release API call failed: %s", exc)
        return None


def _latest_release(token: Optional[str]) -> Optional[dict]:
    """取最新的桌面 App release（tag 以 app-v 開頭）。

    repo 同時有 ``app-v*``（桌面）與 ``ext-v*``（extension）兩種 tag，
    所以不能用 /releases/latest（那會回最近一筆、不分元件），改列出 releases
    過濾 ``app-v`` 前綴後取版號最高者。
    """
    data = _api_get(f"/repos/{repo()}/releases?per_page=30", token)
    if not isinstance(data, list):
        return None
    candidates = []
    for rel in data:
        if rel.get("draft"):
            continue
        tag = rel.get("tag_name") or ""
        if not tag.startswith(TAG_PREFIX):
            continue
        ver = _parse_ver(tag)
        if ver:
            candidates.append((ver, rel))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    return candidates[-1][1]


def _pick_asset(assets: list) -> Optional[dict]:
    """挑安裝檔資產：CodexAutoAI-setup-*.exe（.exe）。回傳 asset dict。"""
    exes = [a for a in assets if str(a.get("name", "")).lower().endswith(".exe")]
    return exes[0] if exes else None


def check_update() -> dict:
    """比對本機版號與最新 GitHub release。永不 raise。"""
    current = local_version()
    token = gh_token()
    result = {
        "current": current,
        "latest": None,
        "tag": None,
        "update_available": False,
        "notes": None,
        "html_url": None,
        "asset_id": None,
        "asset_name": None,
        "auth_ok": False,
        "installed": is_installed(),
        "repo": repo(),
        "error": None,
    }
    rel = _latest_release(token)
    if rel is None:
        result["error"] = (
            "無法讀取 GitHub Release（repo 為 private，需設定 token）"
            if not token else "GitHub API 讀取失敗或尚無桌面 App release"
        )
        return result
    result["auth_ok"] = True
    tag = rel.get("tag_name") or ""
    latest = tag[len(TAG_PREFIX):] if tag.startswith(TAG_PREFIX) else tag.lstrip("v")
    asset = _pick_asset(rel.get("assets") or [])
    result.update({
        "latest": latest,
        "tag": tag,
        "notes": (rel.get("body") or "")[:2000],
        "html_url": rel.get("html_url"),
        "update_available": is_newer(latest, current),
        "asset_id": asset.get("id") if asset else None,
        "asset_name": asset.get("name") if asset else None,
    })
    return result


# ---- apply ---- #


def _download_asset(tag: str, asset_name: str, token: Optional[str],
                    dest: Path) -> bool:
    """把 release 資產下載到 ``dest``。成功回 True。

    優先用 ``gh`` CLI（乾淨處理 private-repo auth + S3 redirect）；失敗再退回
    asset API，並在 cross-origin redirect 時丟掉 Authorization header（否則 S3 拒絕）。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    # 1) gh CLI
    try:
        out = subprocess.run(
            ["gh", "release", "download", tag, "--repo", repo(),
             "--pattern", asset_name, "--output", str(dest), "--clobber"],
            capture_output=True, text=True, timeout=600,
        )
        if out.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            return True
    except (OSError, subprocess.SubprocessError):
        pass

    # 2) 手動：找 asset id → asset API octet-stream → redirect 不再帶 Authorization
    if not token:
        return False
    rel = _api_get(f"/repos/{repo()}/releases/tags/{tag}", token)
    if not rel:
        return False
    asset = next((a for a in (rel.get("assets") or [])
                  if a.get("name") == asset_name), None)
    if not asset:
        return False
    asset_url = f"https://api.github.com/repos/{repo()}/releases/assets/{asset['id']}"

    class _NoAuthRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            new = super().redirect_request(req, fp, code, msg, headers, newurl)
            if new is not None:
                new.headers.pop("Authorization", None)
            return new

    opener = urllib.request.build_opener(_NoAuthRedirect())
    req = urllib.request.Request(asset_url)
    req.add_header("Accept", "application/octet-stream")
    req.add_header("User-Agent", "codexautoai-updater")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with opener.open(req, timeout=600) as resp, open(dest, "wb") as fh:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
        return dest.exists() and dest.stat().st_size > 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("manual asset download failed: %s", exc)
        return False


def apply_update(asset_name: Optional[str] = None) -> dict:
    """下載最新安裝檔並啟動它（Inno Setup 精靈，相同 AppId 就地升級）。

    開發 checkout（非安裝版）會拒絕——請改用 git pull。
    """
    if not is_installed():
        return {"status": "refused",
                "error": "非安裝版（dev checkout）——請用 git pull 更新"}
    info = check_update()
    if not info.get("auth_ok"):
        return {"status": "error", "error": info.get("error") or "無法存取 release"}
    if not info.get("update_available"):
        return {"status": "up-to-date", "current": info["current"]}
    name = asset_name or info.get("asset_name")
    if not name:
        return {"status": "error", "error": "release 沒有可用的安裝檔資產"}

    import tempfile
    tmp = Path(tempfile.gettempdir())
    setup = tmp / f"CodexAutoAI-update-{info['latest']}.exe"
    if not _download_asset(info["tag"], name, gh_token(), setup):
        return {"status": "error", "error": "下載安裝檔失敗（檢查 token / 網路）"}

    # 啟動安裝程式（顯示精靈；per-user Inno Setup，相同 AppId 覆蓋升級）。
    try:
        if os.name == "nt":
            os.startfile(str(setup))  # type: ignore[attr-defined]
        else:
            subprocess.Popen([str(setup)])
    except OSError as exc:
        return {"status": "error", "error": f"啟動安裝程式失敗：{exc}"}
    return {"status": "updating", "from": info["current"], "to": info["latest"],
            "installer": str(setup)}
