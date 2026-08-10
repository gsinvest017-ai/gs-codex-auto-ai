# GUI 驗證測試計畫

## 這份計畫要補的洞

`tests/` 已經涵蓋得不錯（v2 引擎、工具、overlay、enforcement、Windows-only 的
`skipif` 分支都有 CI 在跑）。它涵蓋不到的只有一類東西：**需要一個真的有人登入的
桌面才會發生的事**。

CI 跑在 `ubuntu-latest` 與 `windows-latest`。`windows-latest` 有 Windows API，
但**沒有互動桌面 session**——GitHub 的 runner 是服務身分，跟 SSH 進去一樣落在
非互動的 window station。所以：

- `Tk()` 建得起來嗎？CI 驗不到
- 視窗真的畫到螢幕上了嗎？CI 驗不到
- `winembed.app_is_foreground` 在真實 HWND 上回傳什麼？CI 驗不到
- ConPTY 在真實 session 裡開得起來嗎？CI 只驗得到 ctypes 呼叫形狀

**啟動就壞掉是最嚴重、也最容易漏掉的回歸**，因為它一行 traceback 都不會進 CI，
只會在某個同事早上打開 App 時發生。

## 分層

| 層 | 跑在哪 | 驗什麼 | 現況 |
|---|---|---|---|
| unit | CI（ubuntu + windows） | 純函式、參數組裝、版本比較 | **已存在** |
| **gui-smoke** | 真實桌面節點 | App 起得來、視窗出現、程序不崩 | **本計畫** |
| gui-flow | 真實桌面節點 | 環境檢查／設定修復／pipeline 啟動 | 未來 |
| e2e | 真實桌面節點 + 外部帳號 | 完整七階段跑完 | 需要 Claude／Codex 登入，暫不做 |

本計畫只做 **gui-smoke**。理由：它抓的是最高頻、最痛的回歸，而且不需要任何外部帳號，
所以可以無人化、可以每天跑。

## gui-smoke 的檢查項

| # | 檢查 | 判準（事實，非截圖） | 失敗代表什麼 |
|---|---|---|---|
| 1 | broker 可達 | `gsagent info` 回 `ok` 且列出能力 | 測試環境本身壞了，不是 App 的問題 |
| 2 | 目標有互動桌面 | `interactive=true` 且 `locked=false` | 機器鎖屏／沒人登入，GUI 驗證無意義 → 直接中止而不是判 App 失敗 |
| 3 | App 啟動 | `exec --detach` 回傳 pid | launcher.py 連 python 層都跑不起來 |
| 4 | **視窗出現** | `focus "CodexAutoAI"` 成功 | Tk 視窗沒畫出來、或標題變了 |
| 5 | **程序存活** | 啟動 40 秒後 pid 仍在 | 啟動即崩潰（最常見的回歸） |
| 6 | 截圖 | 取回 PNG | 佐證用，**不作為判定依據** |

第 4 項的判準之所以夠強：broker 的 `focus` 不只是「找到同名視窗」——它會

1. 用 `_NET_CLIENT_LIST` / `EnumWindows` 過濾出 WM 認得的真 toplevel
2. 多個同名視窗時回報歧義錯誤而不是隨便挑一個
3. 切換後**回頭用 `GetForegroundWindow` 確認實際結果**，而不是信任 API 回傳值

所以 `focus` 成功 ⇒ 視窗確實存在、確實是 toplevel、確實在使用者看得到的桌面上。

## 判準原則

**斷言事實，不看截圖。** 截圖會拍、會收進 artifacts，但只是給人看的佐證。

- 像素比對脆弱：字型、DPI、佈景、游標位置都會讓它紅
- 被測機器自己回報「我成功了」沒有意義——它正是可能壞掉的那一方

## 安全前提（這是生產工具）

這個 App 是內部開發人員每天要用的工具，可能有人正開著。因此：

| 風險 | 對策 |
|---|---|
| 關掉同事正在用的 App | **只殺自己啟動的那個 pid**，絕不用 `taskkill /IM pythonw.exe` |
| 影響 CI 紅綠 | 檔案放在 `tests/` 外，CI 跑的是 `pytest tests/ -q`；檔名也不叫 `test_*.py` |
| 改到 repo 狀態 | 只讀不寫，不改任何設定檔 |
| 汙染桌面 | 每輪結束一定收尾，`finally` 保證失敗也會收 |
| 打擾使用者的鍵盤 | 本計畫**完全不注入鍵盤滑鼠**，只做啟動／視窗／截圖 |

最後一條值得強調：gui-smoke 刻意不做輸入注入。輸入是全域的，會打進當下的前景視窗；
在一台可能有人在用的機器上這風險不划算，而 smoke 層也不需要它。
真的要做 gui-flow 時，再用 broker 的 `-W`（切前景與注入為單一原子操作）。

## 執行

```bash
python gui-verify/run.py --node public-pc --repo-path C:/Users/User/gs-codex-auto-ai
```

離開碼 = 失敗數。

## 何時放進每晚的 loop

**先不要。** 建議順序：

1. 在自己機器上手動跑幾輪，確認不會誤報
2. 確認目標機器的桌面狀態穩定（沒有人會隨手鎖屏）
3. 再到 `gs-harness` 的 `registry/repos.toml` 加 `verify` 覆寫

第 2 點是實務上最容易翻車的地方：機器一鎖屏，第 2 項檢查就會中止。
那是正確行為（GUI 驗證在鎖屏機器上本來就沒意義），但如果沒預期到，
會被誤讀成「App 壞了」。
