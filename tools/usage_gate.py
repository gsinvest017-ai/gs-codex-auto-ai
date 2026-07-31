#!/usr/bin/env python3
"""
usage_gate.py — Token 用量閘門：別讓 autopilot 把使用者的額度吃光。

移植自 gs-harness 的 `src/harness/usage.py`（private repo，故**重寫而非 import**：
本框架公開發行、且會被丟進使用者專案直接跑，不能引入私有依賴）。

策略與 gs-harness 相同：訂閱制看不到絕對額度，所以拿「歷史峰值 5h block 的
totalTokens」當 100%，當前 active block 用量超過 `max_block_pct` 就擋下續跑。

## 與 gs-harness 的兩個**刻意差異**（不是漏抄）

gs-harness 的 loop 是排程在深夜無人時跑的，所以它 fail-closed（查不到用量就不跑）
且預設保護白天 09–18 點。CodexAutoAI 的 autopilot 是**使用者自己按下去、坐在電腦前
等結果**的（desktop launcher 的「非停模式」勾選框），套同一套預設會很反直覺：

1. **預設關閉**（`enabled = false`）。要開才開——見下面「啟用方式」。
2. **fail-open**：查不到 ccusage 用量 → **放行**。使用者多半沒裝 ccusage，
   fail-closed 會讓 autopilot 直接不能用。只有在「確實查到用量且超標」時才擋。
3. **`protect_hours` 預設空**（不保護白天）。排程 / 無人值守情境才建議設 `[9, 18]`。

## 啟用方式

環境變數（最快）：

    CODEXAUTOAI_USAGE_GATE=1          # 啟用，用預設門檻 60%
    CODEXAUTOAI_USAGE_MAX_PCT=50      # 改門檻
    CODEXAUTOAI_PROTECT_HOURS=9-18    # 加白天保護時段

或在專案根目錄放 `usage_gate.toml`：

    [usage_gate]
    enabled = true
    max_block_pct = 60
    protect_hours = [9, 18]

## 用法

    python tools/usage_gate.py              # 印判定；exit 0=放行、1=擋下
    python tools/usage_gate.py --json       # 給程式消費
    python tools/usage_gate.py --force      # 覆寫（永遠放行）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_CFG: dict = {
    # 見上方「與 gs-harness 的刻意差異」：這三個預設都與 harness 不同。
    "enabled": False,
    "max_block_pct": 60,
    "protect_hours": [],
}

CONFIG_NAME = "usage_gate.toml"


def _project_dir() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path(__file__).resolve().parent.parent


def _parse_hours(raw: str) -> list[int]:
    """把 `"9-18"` / `"9,18"` 解析成 `[9, 18]`；壞格式回空 list（等於不保護）。"""
    for sep in ("-", ",", ":"):
        if sep in raw:
            parts = [p.strip() for p in raw.split(sep, 1)]
            if all(p.isdigit() for p in parts):
                lo, hi = int(parts[0]), int(parts[1])
                if 0 <= lo <= 24 and 0 <= hi <= 24:
                    return [lo, hi]
            return []
    return []


def load_cfg(root: Path | None = None) -> dict:
    """合併優先序：內建預設 < `usage_gate.toml` < 環境變數。"""
    cfg = dict(DEFAULT_CFG)
    root = root or _project_dir()

    path = root / CONFIG_NAME
    if path.exists():
        try:
            import tomllib
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            cfg.update(data.get("usage_gate", {}))
        except Exception:
            pass                       # 壞設定檔不該讓 pipeline 掛掉

    env = os.environ.get("CODEXAUTOAI_USAGE_GATE")
    if env is not None:
        cfg["enabled"] = env.strip().lower() in {"1", "true", "yes", "on"}
    pct = os.environ.get("CODEXAUTOAI_USAGE_MAX_PCT")
    if pct and pct.strip().isdigit():
        cfg["max_block_pct"] = int(pct.strip())
    hours = os.environ.get("CODEXAUTOAI_PROTECT_HOURS")
    if hours:
        parsed = _parse_hours(hours)
        if parsed:
            cfg["protect_hours"] = parsed
    return cfg


class Usage:
    """一次 ccusage 查詢的結果。（手寫 __init__，不用 dataclass — 見 run_loop.Ran）"""

    def __init__(self, active_tokens: int, peak_tokens: int, cost_usd: float = 0.0) -> None:
        self.active_tokens = active_tokens
        self.peak_tokens = peak_tokens
        self.cost_usd = cost_usd

    @property
    def pct(self) -> float:
        if self.peak_tokens <= 0:
            return 0.0            # 無歷史峰值 → 不推論成 100%（fail-open）
        return round(self.active_tokens / self.peak_tokens * 100, 1)

    def as_dict(self) -> dict:
        return {"active_tokens": self.active_tokens, "peak_tokens": self.peak_tokens,
                "cost_usd": self.cost_usd, "pct": self.pct}


def fetch_usage(timeout: int = 60) -> Usage | None:
    """讀 `ccusage blocks --json`；ccusage 不在 / 失敗 / 無歷史 → None（呼叫端 fail-open）。"""
    exe = shutil.which("ccusage")
    if exe is None:
        return None
    try:
        proc = subprocess.run([exe, "blocks", "--json"], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, shell=False)
        if proc.returncode != 0:
            return None
        blocks = [b for b in json.loads(proc.stdout).get("blocks", [])
                  if not b.get("isGap")]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, ValueError):
        return None
    active = next((b for b in blocks if b.get("isActive")), None)
    history = [b.get("totalTokens", 0) for b in blocks if not b.get("isActive")]
    if not history:
        return None
    return Usage(
        active_tokens=(active or {}).get("totalTokens", 0),
        peak_tokens=max(history),
        cost_usd=(active or {}).get("costUSD", 0.0) or 0.0,
    )


def evaluate_gate(usage: Usage | None, now_hour: int, cfg: dict,
                  force: bool = False) -> tuple[bool, str]:
    """回傳 `(可以續跑嗎, 原因)`。純函式，方便測試。"""
    if not cfg.get("enabled"):
        return True, "usage gate 未啟用（預設關閉；設 CODEXAUTOAI_USAGE_GATE=1 開啟）"
    if force:
        return True, "--force 覆寫 gate"

    hours = cfg.get("protect_hours") or []
    if len(hours) == 2:
        lo, hi = hours
        if lo <= now_hour < hi:
            return False, (f"現在 {now_hour} 點，在保護時段 [{lo}, {hi})，"
                           "不啟動新一輪（--force 可覆寫）")

    if usage is None:
        # 與 gs-harness 相反：這裡 fail-open。詳見模組 docstring。
        return True, "查不到 ccusage 用量，fail-open 放行（裝了 ccusage 才會真的把關）"

    threshold = cfg.get("max_block_pct", 60)
    if usage.pct >= threshold:
        return False, (f"當前 block 用量 {usage.pct}% 已達門檻 {threshold}%"
                       f"（{usage.active_tokens:,} / 峰值 {usage.peak_tokens:,} tokens），"
                       "保留額度給你的手動工作")
    return True, f"block 用量 {usage.pct}% < 門檻 {threshold}%，放行"


def check(force: bool = False, root: Path | None = None) -> tuple[bool, str]:
    """便捷入口：載設定 + 查用量 + 判定。"""
    cfg = load_cfg(root)
    # gate 沒開就別花時間跑 ccusage（它要起一個 node 行程）。
    usage = fetch_usage() if cfg.get("enabled") else None
    return evaluate_gate(usage, datetime.now().hour, cfg, force)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CodexAutoAI token 用量閘門")
    ap.add_argument("--force", action="store_true", help="覆寫閘門（永遠放行）")
    ap.add_argument("--json", action="store_true", help="輸出 JSON")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    allowed, reason = check(force=args.force)
    if args.json:
        print(json.dumps({"allowed": allowed, "reason": reason}, ensure_ascii=False))
    else:
        print(f"{'放行' if allowed else '擋下'}：{reason}")
    return 0 if allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
