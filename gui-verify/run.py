#!/usr/bin/env python3
"""在遠端真實桌面上啟動 CodexAutoAI 並驗證它真的活著。

判準原則（沿用 gs-interop-lab）：**斷言事實，不看截圖**。
截圖會拍會收，但那是給人看的佐證；判定依據是「視窗是不是真的 toplevel、
程序還在不在」這種是非題。

安全原則（這個 App 是內部生產工具，可能有人正開著）：
  - 只殺自己啟動的那個 pid，絕不用 image name 掃殺
  - 只讀 repo，不寫入、不改設定
  - 失敗也要收尾

刻意不放進 tests/：CI 跑的是 `pytest tests/ -q`，這支工具需要一台真實桌面機器，
不該影響 CI 紅綠。檔名也不叫 test_*.py，裸跑 pytest 同樣撿不到。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

WINDOW_TITLE = "CodexAutoAI"
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=accept-new"]

PASS = FAIL = SKIP = 0


def ok(m: str) -> None:
    global PASS
    PASS += 1
    print(f"  [OK]   {m}")


def no(m: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {m}")


def sk(m: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {m}")


def hdr(m: str) -> None:
    print(f"\n{m}")


def sh(argv: list[str], timeout: int = 180) -> tuple[int, str, str]:
    """執行指令並取回輸出。輸出走暫存檔而非 PIPE。

    Windows 上當這支腳本本身是透過 SSH 執行的（放進 nightly loop 後就會是），
    ssh.exe 的 stdout 接到 pipe 不會正常收尾，呼叫端會一路等到 timeout。
    stdin=DEVNULL 同理：ssh 會轉送 stdin 直到 EOF，巢狀執行時那個 EOF 不會來。
    """
    d = tempfile.mkdtemp(prefix="gui-verify-")
    op, ep = os.path.join(d, "out"), os.path.join(d, "err")
    try:
        try:
            with open(op, "wb") as o, open(ep, "wb") as e:
                rc = subprocess.run(argv, timeout=timeout, stdin=subprocess.DEVNULL,
                                    stdout=o, stderr=e).returncode
        except subprocess.TimeoutExpired:
            return -1, "", f"timeout after {timeout}s"
        except FileNotFoundError as exc:
            return 127, "", str(exc)

        def read(p: str) -> str:
            try:
                with open(p, "rb") as fh:
                    return fh.read().decode("utf-8", "replace")
            except OSError:
                return ""

        return rc, read(op), read(ep)
    finally:
        shutil.rmtree(d, ignore_errors=True)


class Node:
    def __init__(self, alias: str, gsagent: str) -> None:
        self.alias = alias
        self.gsagent = gsagent

    def gs(self, args: str, timeout: int = 180) -> dict:
        rc, out, err = sh(["ssh", *SSH_OPTS, self.alias, f"{self.gsagent} {args}"], timeout)
        try:
            return json.loads(out)
        except ValueError:
            return {"ok": False, "error": (out + err).strip()[:400] or "無輸出"}

    def raw(self, args: str, timeout: int = 180) -> tuple[int, str, str]:
        return sh(["ssh", *SSH_OPTS, self.alias, f"{self.gsagent} {args}"], timeout)

    def fetch(self, remote: str, local: str) -> bool:
        return sh(["scp", *SSH_OPTS, f"{self.alias}:{remote}", local], 180)[0] == 0


def q(s: str) -> str:
    return '"' + str(s).replace('"', '\\"') + '"'


def main() -> int:
    ap = argparse.ArgumentParser(description="在遠端真實桌面驗證 CodexAutoAI 啟動")
    ap.add_argument("--node", required=True, help="目標機器的 ssh 別名")
    ap.add_argument("--repo-path", required=True, help="這個 repo 在該機器上的路徑")
    ap.add_argument("--gsagent", default="gsagent")
    ap.add_argument("--artifacts", default="gui-verify/artifacts",
                    help="截圖存放處（預設在 gui-verify/ 底下，已被本目錄的 .gitignore 排除）")
    ap.add_argument("--keep", action="store_true", help="驗完不要關掉 App（除錯用）")
    ap.add_argument("--launch-timeout", type=int, default=40)
    a = ap.parse_args()

    node = Node(a.node, a.gsagent)
    os.makedirs(a.artifacts, exist_ok=True)
    launched_pid: int | None = None

    print(f"gui-verify — 目標 {a.node}:{a.repo_path}")

    # ── 1. 前置：broker 活著且真的有桌面 ────────────────────
    hdr("1. broker 與桌面")
    info = node.gs("info", timeout=90)
    if not info.get("ok"):
        no(f"連不上 {a.node} 的 broker：{info.get('error')}")
        print("\n先確認該機器已安裝 gs-mac-agent-perms 的 broker（scripts/install.ps1）")
        return 1
    caps = info.get("capabilities") or []
    ok(f"broker 回應（{info.get('hostname')}，{len(caps)} 項能力）")

    notes = info.get("notes") or {}
    if notes.get("interactive") and not notes.get("locked"):
        ok(f"目標在互動桌面上（session {notes.get('session_id')}，{notes.get('window_station')}）")
    else:
        no(f"目標沒有可用的互動桌面（interactive={notes.get('interactive')} "
           f"locked={notes.get('locked')}）——GUI 驗證在這種狀態下沒有意義")
        return 1

    for need in ("focus", "screenshot", "exec"):
        if need not in caps:
            no(f"broker 未回報 {need} 能力，無法進行 GUI 驗證")
            return 1

    # ── 2. 啟動 App ─────────────────────────────────────────
    hdr("2. 啟動 App")
    launcher = f"{a.repo_path.rstrip('/')}/desktop/launcher.py"
    # pythonw：不要在桌面上彈出一個主控台視窗
    r = node.gs(f"exec --detach -- pythonw {q(launcher)}", timeout=120)
    if not r.get("ok"):
        no(f"啟動失敗：{r.get('error')}")
        return 1
    launched_pid = r.get("pid")
    ok(f"已啟動（pid={launched_pid}）")

    try:
        # ── 3. 視窗真的出現了嗎（核心斷言）──────────────────
        hdr("3. 視窗")
        deadline = time.time() + a.launch_timeout
        focused = None
        while time.time() < deadline:
            time.sleep(2)
            focused = node.gs(f"focus {q(WINDOW_TITLE)}", timeout=90)
            if focused.get("ok"):
                break
        if focused and focused.get("ok"):
            # focus 成功代表：視窗存在、是 WM 認得的 toplevel、而且真的切到前景了。
            # broker 的 focus 會回頭確認 GetForegroundWindow，不是信任 API 回傳值。
            ok(f"視窗出現且可帶到前景（{focused.get('matched')}）")
        else:
            no(f"{a.launch_timeout} 秒內沒有出現標題含「{WINDOW_TITLE}」的視窗："
               f"{(focused or {}).get('error')}")
            wins = (focused or {}).get("visible_windows")
            if wins:
                print(f"       當時桌面上的視窗：{', '.join(map(str, wins[:8]))}")

        # ── 4. 程序還活著（沒有啟動即崩潰）──────────────────
        hdr("4. 程序狀態")
        if launched_pid:
            rc, out, _ = node.raw(
                f'exec --json -- powershell -NoProfile -Command '
                f'"if (Get-Process -Id {launched_pid} -EA SilentlyContinue) '
                f'{{ Write-Output ALIVE }} else {{ Write-Output GONE }}"', timeout=90)
            alive = "ALIVE" in out
            if alive:
                ok(f"pid {launched_pid} 仍在執行（沒有啟動即崩潰）")
            else:
                no(f"pid {launched_pid} 已經不在了——App 啟動後隨即結束")

        # ── 5. 截圖佐證（不作為判定依據）────────────────────
        hdr("5. 截圖佐證")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        remote_png = f"C:/Windows/Temp/gui-verify-{stamp}.png"
        shot = node.gs(f"screenshot {q(remote_png)}", timeout=180)
        if shot.get("ok"):
            local = os.path.join(a.artifacts, f"{a.node}-{stamp}.png")
            if node.fetch(remote_png, local):
                ok(f"已取回：{local}（{shot.get('bytes')} bytes）")
            else:
                sk("截圖成功但取回失敗（不影響判定）")
            node.gs(f"exec -- cmd.exe /c del {q(remote_png.replace('/', chr(92)))}", timeout=60)
        else:
            sk(f"截圖失敗（不影響判定）：{shot.get('error')}")

    finally:
        # ── 6. 收尾 ─────────────────────────────────────────
        hdr("6. 收尾")
        if a.keep:
            sk(f"已指定 --keep，保留 pid {launched_pid} 不關閉")
        elif launched_pid:
            # 只殺我們自己啟動的那個 pid。
            # **絕不用 taskkill /IM pythonw.exe** —— 這個 App 是內部生產工具，
            # 用 image name 掃殺會連同事正開著的那個一起關掉。
            node.gs(f"exec -- cmd.exe /c taskkill /F /PID {launched_pid}", timeout=90)
            ok(f"已關閉自己啟動的 pid {launched_pid}（未使用 image name 掃殺）")

    print(f"\n結果：{PASS} 通過 / {FAIL} 失敗 / {SKIP} 略過")
    if FAIL == 0:
        print("App 在真實桌面上啟動正常。")
    return FAIL


if __name__ == "__main__":
    sys.exit(main())
