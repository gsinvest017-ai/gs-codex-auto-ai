# 動畫產物與 Claude 觀測缺口：2026-09-10

唯讀調查對象是 `C:/Users/User/Desktop/codexautoai-sandbox`，run `7364c192-3aca-4b5d-9d85-e938663844de`，實際 runner invocation 為 `d778e36c4f82461daf27f36f2deaa2dd`。當次是 self-def 三節點圖；本調查沒有修改 trust、憑證或專案產物，也沒有停止行程。

## 警告不等於阻塞

規劃節點 Codex 完成，CLI 用量為 input 323087、output 5274、cached input 270720。第一個 Claude 實作 attempt 執行 1800 秒後 timeout；其 raw log `claude_rrytq1mw.log` 僅 293 bytes，內容是未信任工作區而忽略 permissions.allow 的警告。第二次 attempt 的 `claude_0n7rxsim.log` 也是同一警告。

但 Claude 專案 session 證明實際持續工作，不能把最後一行警告當成 timeout 根因：

- session `f76683d3-a51b-43fd-92b3-f4e9442db882` 從 06:43:58Z 至 07:13:35Z 有 Read、Write、Bash 等 42 次工具紀錄。首個 user prompt 包含同一需求、實作節點文字，以及 planner 的精確 `codex_tphg5azn.log` 交接路徑，與第一個 attempt 相符。
- 第二個 session `da7f1aa8-2b0f-4e1a-9425-1016a0a2c777` 從 07:14:04Z 開始，有相同的需求與交接識別。07:20:34Z 的工具輸出顯示動畫建置 EXIT=0；07:20:47Z 回報 `20 passed, 271 subtests passed in 10.23s`。
- 當時行程鏈為 runner PID 54236 → Claude PID 46448；它不是只有警告而完全沒有工作的狀態。後續單次檢查 session mtime 仍為 07:20:47Z，不能由短暫未更新推定掛死，也不能由上述進度預先宣稱整張圖已交付。

可確定第一個 attempt 因執行時限終止；無足夠證據斷言 trust 警告是致因。現有非串流 stdout 在最終回覆之前可能無工具／用量資料，UI 只讀該檔會看不到已在 session 中發生的工作。不得因此依警告文字自動更改 trust。

## GIF 實際存在

`assets/home-walk/walk.gif` 為 948652 bytes，檔案標頭 GIF89a。唯讀 Pillow 解析確認 640×480、220 frames、首幀 duration 80 ms、loop=0；不是只有副檔名的空檔。`log` 另有兩個較小驗證 GIF，不能當成主要交付替代品。

原 Workbench 產物 allowlist 未含 `.gif`，所以「磁碟已產生」與「UI 沒列出／不播放」可以同時成立。檔案結構驗證仍不代表瀏覽器播放或步態視覺品質已驗證。

## 用量恢复的驗證條件

兩個 session 都有 message usage；同一 `message.id` 會重複出現在多行。可恢復的是當次 session 已觀測的 message 用量，須先核對 cwd、起始時間、首需求與交接路徑，不能把同專案其他會話或整個帳號的用量算進來。

彙總須依 message ID 去重，保留 input、output、cache read、cache creation 各自口徑；不得把 iterations 或 thinking 子項再加一次。尚無 final result 時標示部分觀測；若後來收到 final CLI aggregate，應替代或分列來源，不能與已恢復值相加。此時 events 的 Claude usage=null 表示 CLI 尚未回報，不等於零用量。

目前的測試缺口包括：只有 warning 的 stdout 但 transcript 正在工作、重複 message ID、retry 使用新 session、final aggregate 到來時不重複計量，以及 GIF 產物發現與瀏覽器播放。修正後需用受控資料與正式解析器覆蓋，不能用改 trust 或虛構 token 值讓畫面通過。

## 熱修已完成的驗證

已用 Pillow 唯讀確認主要 GIF 為 640×480、220 幀。正式 Dashboard 的唯讀瀏覽器 harness 已成功列出 `assets/home-walk/walk.gif` 及開啟按鈕。`code --reuse-window` 指令已送出且退出碼為 0；這不證明原生 VS Code 已完成 GIF 渲染或播放驗收。

Node 全套 51 passed。0.17.1 VSIX 封裝共 140 entries，31 組 runtime 原始碼與封裝內容逐位元組比對一致；此項僅記錄已完成的封裝驗證，不代表安裝結果。

獨立 review 確認一般 trust warning 不再觸發 fatal；真正的登入拒絕與終止權限錯誤才分類為 blocker，成功結果中曾出現的 permission denial 不會直接推翻成功。受控回歸覆蓋 warning 後持續工作、timeout 保留部分用量、同 message ID 更新去重、忽略 thinking／iterations 重複項，以及 final aggregate 替代 partial。相關 blocker 與 Python／JS parity 合計 52 passed；這與其他測試組可能重疊，不相加。

新增 usage_partial 與 cache_creation_input_tokens 保留來源差異；cache creation 不混入 cache read，既有未知用量未被任意補值。硬時限仍保留每次 attempt 30 分鐘上限，串流觀測與有活動都不會取消此上限；本次修復不將先前 timeout 改寫成成功。

本次全套 Python 檢查曾得到 3 failed、1377 passed、4 skipped：兩項仍期待舊版 JSON argv，另一項為帶 permission_denials、但沒有明確成功狀態的裸 result 不能直接放行。此紀錄保留為實際檢查結果，不宣稱全套全綠；修正後的針對性重驗另列，不能抹去此次失敗紀錄。

修正後後端針對性回歸 100 passed；獨立 review 的 startup blocker 組 14 passed。裸 result 帶拒絕紀錄時，只有 `type=result`、`subtype=success`、`is_error=false` 同時成立才視為明確成功。這些結果不是全套重新通過的宣稱。

目前 GLB viewer 尚未使用 AnimationMixer，僅靜態預覽模型，未驗證 GLB 動畫播放。本次提供的是 GIF 動畫入口；不以可開啟 GLB 冒充已播放其動畫。

## 最終交付結果

修正後完整重跑：1384 passed、4 skipped、2 subtests passed（226.89 秒），JUnit 紀錄為 `dist/pytest-0.17.1.xml`；前述三項失敗已修復。Node 全套 51 passed。0.17.1 封裝的 140 entries 與 31 組 runtime 位元組一致性檢查通過，並已由 code install 成功安裝。未執行 Reload Window、未干擾舊 run；安裝不代表進行中的舊行程已使用新版程式碼。
