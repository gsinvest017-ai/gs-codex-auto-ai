---
name: qaqa
description: "qaqa：對當前專案進行完整品質審查（SDD spec + 雙軌測試 + 三方審查）"
---

# QA Review Skill

對當前專案進行 QA Loop 風格的完整品質審查。

## 執行流程

### Step 1：掃描專案結構

掃描當前工作目錄的所有原始碼檔案：
- 列出所有 `.py`、`.js`、`.ts`、`.yaml`、`.json` 檔案
- 統計 LOC（程式碼行數）
- 識別 entry points、config、tests

### Step 2：Phase 1 — Propose（問題發現）

以三個維度掃描專案：

**A. 安全性掃描**
- 硬編碼的 API keys / secrets
- shell=True 的 command injection
- 未驗證的用戶輸入（path traversal, SSRF, XSS）
- 不安全的 eval/exec
- 缺少 .gitignore 保護

**B. 程式碼品質掃描**
- bare except 捕獲
- 缺少錯誤處理（I/O, 網路, subprocess）
- 資源未清理（file handles, connections, browsers）
- print() 取代 logging
- 缺少 type hints / docstrings
- 重複邏輯（應提取為共用函數）

**C. 架構掃描**
- 模組間耦合度
- 循環依賴
- 單點故障
- 配置硬編碼
- 缺少的生產必要功能（retry, rate limit, graceful shutdown）

### Step 3：Phase 2 — Review（三方審查）

對每個發現的問題進行嚴重度分類：

| 級別 | 定義 | 處理 |
|------|------|------|
| **P0 CRITICAL** | 安全漏洞、資料損壞風險 | 必須立即修 |
| **P1 HIGH** | 生產環境會 crash | 上線前必修 |
| **P2 MEDIUM** | 功能缺失、UX 問題 | 計劃修復 |
| **P3 LOW** | 程式碼品質、技術債 | 持續改善 |

### Step 4：Phase 3 — 產出 SDD Spec

對每個 P0/P1 問題產出修復規格：

```yaml
issue_id: "QR-{序號}"
title: "問題標題"
severity: "P0|P1"
problem:
  description: "問題描述"
  file: "檔案:行數"
root_cause: "根因"
fix:
  strategy: "修復策略"
  scope: ["需修改的檔案"]
invariants: ["修復後必須成立的不變量"]
regression: ["需要新增的測試"]
```

### Step 5：Phase 4 — 產出報告

輸出結構化報告：

```
=== QA REVIEW REPORT ===
Project: {專案名稱}
Files scanned: {數量}
LOC: {行數}

--- FINDINGS ---
P0 CRITICAL: {數量}
P1 HIGH: {數量}
P2 MEDIUM: {數量}
P3 LOW: {數量}

--- TOP ISSUES ---
1. [P0] {issue} — {file:line}
2. [P1] {issue} — {file:line}
...

--- SPECS ---
{每個 P0/P1 的 SDD spec}

--- RECOMMENDATIONS ---
{改善建議，按優先級排序}
```

### Step 6：Phase 5 — 自動修復（可選）

如果使用者同意，對 P0 issues 直接修復：
1. 讀取相關檔案
2. 用 Edit tool 修改
3. 驗證修改後仍可編譯

## 橋接 QA Loop 系統

此 skill 也可以呼叫 QA Loop 系統：

```bash
# 如果 QA Loop exe 可用（請將 <QALOOP_HOME> 換成你的安裝位置）
<QALOOP_HOME>\dist\QALoop.exe --config <QALOOP_HOME>\configs\default.yaml --project-dir .
```

## 使用方式

在 Claude Code 中輸入：
```
/qa-review
```
