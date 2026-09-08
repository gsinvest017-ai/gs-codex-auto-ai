"""Offline draft generator for public installations; Python stdlib only.

This is an original fallback, not gs-spec-forge or a RAG client.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import uuid


def seed(intent: str, vault: Path) -> Path:
    """Write a unique draft; never overwrite an existing spec."""
    intent = intent.strip()
    if not intent:
        raise ValueError("需求不可空白")
    now = datetime.now(timezone.utc)
    folder = vault.expanduser().resolve() / ("seed-" + now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12])
    folder.mkdir(parents=True, exist_ok=False)
    target = folder / "spec.md"
    quoted = "\n".join("> " + line for line in intent.splitlines())
    content = f"""# 需求規格草稿

產生時間：{now.isoformat()}
來源：CodexAutoAI 離線 fallback；未呼叫模型，未檢索外部資料。
狀態：draft。後續需求階段 MUST 依原始意圖細化規格與驗收條件。

## 原始需求

{quoted}

## 範圍與假設

- 原始需求是工作範圍依據；不得把本草稿當成已完成需求分析。
- 未指定的介面、資料來源、供應商、模型與部署方式仍待需求階段判定。
- 未進行 RAG 檢索，沒有可聲稱已驗證的外部引用。

### Requirement: INTENT-R1 — 完成使用者意圖

系統 SHALL 實現上方原始需求；需求階段 MUST 將其拆成可驗證的功能與限制。

#### Scenario: 依需求交付

- GIVEN 上方原始需求與專案目前狀態
- WHEN pipeline 完成需求分析與實作
- THEN 交付物符合細化後的驗收條件，並提供實際驗證結果

### Requirement: VALIDATION-R1 — 驗證與誠實交付

交付報告 MUST 區分已驗證成果、假設與尚未完成事項。

#### Scenario: 尚有未驗證項目

- GIVEN 有無法在目前環境驗證的需求
- WHEN 產生交付報告
- THEN 列出該限制與所需後續驗證，不將其標為通過
"""
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("seed")
    command.add_argument("intent")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        target = seed(args.intent, Path(os.environ.get("SPEC_VAULT") or Path.cwd() / "vault"))
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
