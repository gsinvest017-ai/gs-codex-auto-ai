#!/usr/bin/env python3
"""gen_manifest.py — 產生 auto-update 用的 latest.json。

放到公開鏡像 repo（gs-codex-auto-ai-releases）的 main 分支，供 updater 走
raw.githubusercontent 讀取——靜態檔不吃 api.github.com 的 60/hr 匿名限額，
對「沒裝 gh / 公司 NAT 共用對外 IP」的使用者可靠得多。

版號預設讀 desktop/VERSION（桌面 App）與 vscode-extension/package.json（extension），
也可用 --app / --ext 覆寫。

用法：
    python tools/gen_manifest.py                 # 讀本機版號，印 JSON
    python tools/gen_manifest.py --app 0.2.3 --ext 0.2.3
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MIRROR = "gsinvest017-ai/gs-codex-auto-ai-releases"
ROOT = Path(__file__).resolve().parent.parent


def _app_version() -> str:
    txt = (ROOT / "desktop" / "VERSION").read_text(encoding="utf-8").strip()
    return txt.splitlines()[0].strip()


def _ext_version() -> str:
    pkg = json.loads((ROOT / "vscode-extension" / "package.json").read_text(encoding="utf-8"))
    return str(pkg["version"]).strip()


def build(app_ver: str | None = None, ext_ver: str | None = None,
          mirror: str = MIRROR) -> dict:
    app_ver = app_ver or _app_version()
    ext_ver = ext_ver or _ext_version()
    base = f"https://github.com/{mirror}/releases/download"
    return {
        "app": {
            "version": app_ver,
            "tag": f"app-v{app_ver}",
            "exe": f"{base}/app-v{app_ver}/CodexAutoAI-setup-{app_ver}.exe",
        },
        "ext": {
            "version": ext_ver,
            "tag": f"ext-v{ext_ver}",
            "vsix": f"{base}/ext-v{ext_ver}/codexautoai-{ext_ver}.vsix",
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="產生 auto-update 用的 latest.json")
    ap.add_argument("--app")
    ap.add_argument("--ext")
    ap.add_argument("--mirror", default=MIRROR)
    args = ap.parse_args(argv)
    print(json.dumps(build(args.app, args.ext, args.mirror),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
