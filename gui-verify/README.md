# gui-verify — 在真實桌面上驗證這個 App 真的起得來

## 這是什麼

一組**完全獨立、選用**的檢查，在一台真實的 Windows 桌面機器上啟動 CodexAutoAI，
確認它真的出現在畫面上、視窗是活的、然後收乾淨。

## 為什麼需要它

`tests/` 裡的單元測試驗得了函式，驗不了「App 到底起不起得來」。而這個 repo 最脆弱的
部分恰好都在那條線的另一邊：

| 模組 | 單元測試驗得到 | 驗不到 |
|---|---|---|
| `desktop/launcher.py` | 純函式、參數組裝 | Tk 視窗到底有沒有出現 |
| `desktop/winembed.py` | `app_is_foreground` 的邏輯 | 真實 HWND 的前景狀態 |
| `desktop/conpty.py` | ctypes 呼叫形狀 | ConPTY 真的開得起來 |
| `desktop/updater.py` | 版本比較 | 更新橫幅真的畫出來 |

這支工具是給「這是內部開發人員每天要用的生產工具」這個現實準備的：
**啟動就壞掉是最嚴重、也最容易漏掉的回歸**，因為它一行 traceback 都不會進 CI。

## 這不會影響現有的任何東西

刻意做成**純加法**：

- 沒有修改任何既有檔案
- 檔案全部在 `tests/` **之外**，而 CI 跑的是 `python -m pytest tests/ -q`，
  所以這裡的東西不會被收集、不會影響 CI 紅綠
- 檔名不叫 `test_*.py`，即使有人在 repo 根目錄裸跑 `pytest` 也不會被撿到
- 執行時只讀這個 repo，不寫入、不改設定
- 收尾只殺**自己啟動的那個 pid**，不會用 image name 掃殺——你自己開著的
  CodexAutoAI 不會被關掉

## 需要什麼

一台裝好 [`gs-mac-agent-perms`](https://github.com/gsinvest017-ai/gs-mac-agent-perms)
broker 的 Windows 機器，而且你的機器能免密 SSH 過去。broker 提供的是
「在別台機器的互動桌面上截圖／操作／執行」的能力——SSH session 自己做不到這件事
（sshd 建立的 logon session 落在 `Service-0x2-XXXX$` window station，看不到使用者的桌面）。

那台機器上也要有這個 repo 的 checkout。

## 用法

```bash
python gui-verify/run.py --node public-pc --repo-path C:/Users/User/gs-codex-auto-ai
```

參數：

| 參數 | 預設 | 說明 |
|---|---|---|
| `--node` | 必填 | 目標機器的 ssh 別名 |
| `--repo-path` | 必填 | 這個 repo 在該機器上的路徑 |
| `--gsagent` | `gsagent` | 該機器上的 gsagent 指令 |
| `--artifacts` | `gui-verify/artifacts` | 截圖存放處（已被 `gui-verify/.gitignore` 排除） |
| `--keep` | 否 | 驗完不要關掉 App（除錯用） |

離開碼 0 = 全過，非 0 = 失敗數。

## 接到 gs-harness

在 `gs-harness` 的 `registry/repos.toml` 加一行 `verify` 覆寫即可讓 nightly-verify
一併跑這個（需要 gs-harness 的遠端節點支援）：

```toml
[repos.gs-codex-auto-ai]
verify = "python gui-verify/run.py --node public-pc --repo-path C:/Users/User/gs-codex-auto-ai"
```

**先別加。** 建議等這支工具在你自己機器上手動跑穩幾輪，再放進每晚的 loop。

## 判準原則

沿用 `gs-interop-lab` 的主張：**斷言事實，不看截圖**。

截圖會拍、會收進 artifacts，但它是給人看的佐證，不是判定依據。判定依據是
「視窗存不存在、是不是真的 toplevel、程序還活著沒」這種是非題。

理由：像素比對脆弱（字型、DPI、佈景、游標位置都會讓它紅），而且被測機器自己說
「我成功了」沒有意義——它正是可能壞掉的那一方。
