# CodexAutoAI Workbench 0.15.0

這版保留原有「產生規格 → dispatcher → worker」執行入口，新增可檢查的任務工作區、產物預覽與可選 MCP 接口。模型仍由既有路由與額度政策決定：平常使用 Codex／Claude，只有兩個主線的額度證據皆耗盡才允許 OpenCode 備援。

## 控制台與產物

控制台將使用者正在編輯的下一個需求、本次實際執行需求、階段進度、原生工具活動與模型用量分開呈現。歷史用量不作為本次任務合規證據；未回報欄位保留未知，exit code 為零也不能單獨證明已交付。

「產物」清單從中央 Workbench API 讀取工作區掃描與登記結果。點選 GLB／glTF 會在 VS Code 右側開啟互動 3D 預覽；PNG 等圖片使用 VS Code 圖片預覽，OBJ 使用原始檔編輯器並明示不是互動 3D viewer。

新的 GLB／glTF 在檔案大小與修改時間穩定後自動開啟一次，不搶目前輸入焦點。已開啟的模型變更時重新載入場景，保留旋轉、縮放及平移視角；按「適合視窗」可重新取景。關閉模型後不會因同一檔案更新反覆彈開，仍可從產物清單手動開啟。完成任務後再打開控制台，也會發現現有模型。

自動發現排除 `log`、`tests`、`test`、`node_modules`、`.venv` 等測試或依賴資料夾，避免匯出測試產物造成視窗暴增。明確登記的產物仍可由清單手動選取。模型及依賴限於本工作區內的實體路徑，不能用 symlink 或 `..` 指向外部。

viewer 使用隨 extension 打包的 Three.js 0.186.0、GLTFLoader 與 OrbitControls，保留 MIT 授權；不使用 CDN、不執行使用者專案中的 JavaScript，也不載入遠端 glTF URL。GLB 與 glTF 的本機 buffer／圖片依賴先經 host 檢查再傳入 viewer。總大小上限 100 MB。需要額外解碼器的壓縮模型可能無法顯示，此時會保留可讀錯誤。沒有 WebGL 時也會明示原因。

## 共用 API 與事件

`tools/workbench.py` 是控制台與 MCP 共用的資料入口，避免各端分別解讀日誌。

```powershell
python tools/workbench.py --root C:/path/to/project --action status
python tools/workbench.py --root C:/path/to/project --action activity
python tools/workbench.py --root C:/path/to/project --action artifacts
```

產物登記 append 到 `log/workbench-artifacts.jsonl`，記錄相對路徑、SHA-256、檔案大小與時間。登記只是當時檔案的 metadata，**不複製 snapshot、不代表任務成功**。模型 preview 的成功載入與原生 dispatcher／worker 完成事件是不同證據。

## 可選 MCP

支援 MCP server definition provider 的 VS Code 會在 extension 啟動時提供 `CodexAutoAI Workbench` 本機 stdio server。API 以 feature detection 啟用；舊版 VS Code 缺少此 API 時，原有控制台、任務啟動與模型預覽仍可使用。設定僅屬於 extension provider，不改寫使用者全域 MCP 設定。

同一 server 也可由其他相容 MCP host 自行設定：

```powershell
python tools/workbench_mcp.py --root C:/path/to/project
```

預設只提供讀取工具、路由預覽與 JSON resources；不啟動模型、不接管 host 的模型選擇、不自動接續或重跑任務。CLI 端若明確加上 `--allow-reports`，才會增加 `report_progress` 與 `register_artifact` 這兩個 append-only 工具。它們不能修改路由或執行模型；呼叫端自行回報的進度會標示為未驗證，不會完成任何 pipeline phase。

## 驗證

單元測試涵蓋本機 glTF 依賴、遠端及越界 URI 拒絕、檔案穩定等待、自動開啟一次、保留面板、MCP 新舊 API 相容與既有 routing／dashboard regression。

本機視覺 QA 使用正式 viewer HTML、bundled JavaScript 與 `readModel()`：

```powershell
node tests/tools/preview3d_fixture.js 8767 C:/path/to/project assets/model.glb
```

此 harness 只綁 loopback，用於驗證實際模型渲染、旋轉、縮放、fit 與 reload；不包含在 VSIX 中。
