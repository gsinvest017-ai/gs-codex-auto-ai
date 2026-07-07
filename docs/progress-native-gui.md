# 進度：控制台原生 GUI 化（0.10.0）

目標：讓控制台更像 Claude/Codex 官方 VS Code extension、對非開發者更友善。survey 見對話。
全部 additive、不改 pipeline 行為。

## Milestones
- [x] **M1 C2 主題變數化** — dashboard.js CSS 全面採 `--vscode-*`（保留品牌金給標題/進度），自動跟 IDE 亮/暗一致。
- [x] **M2 C3 狀態列指示** — StatusBar 顯示當前 phase/狀態。
- [x] **M3 C1 側欄常駐** — 活動列 icon + WebviewView，永遠點得到、不會關掉。
- [x] **M4 C4 Walkthrough** — 開機 Getting Started 三步引導。

## 決策
- 不照抄 chat+diff（開發者取向）；借原生整合質感（主題/側欄/狀態列/引導），保留表單式低門檻設計。
- 不採 VSCode Elements（與 strict CSP + inline 零依賴約束衝突）；純 `--vscode-*` CSS 變數達 90% 原生質感。
- 側欄（M3）用 WebviewView，與現有 WebviewPanel 共用 html()；生命週期（onDidChangeVisibility）需獨立驗證。

## 日誌
- M1: dashboard.js CSS 改 --vscode-*。commit 389229a。
- M2: extension.js StatusBarItem + dashboard.computeState/PHASES export。42/42。commit edd1341。
- M3: wireDashboard 抽共用接線；openDashboard(面板)+makeDashboardViewProvider(側欄) 共用；
  package.json 加 viewsContainers/views + resources/codexautoai.svg 活動列 icon。42/42。

- M4: package.json walkthroughs(4 步)+resources/walkthrough/*.md media；bump 0.10.0。
