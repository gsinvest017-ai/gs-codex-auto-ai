不要建立 3D 或 Blender 模型。
請只把清單 alpha、beta、gamma 依照原順序寫入 `names.txt`，每行一個名稱。
產物：names.txt 與 evidence.txt；不應新增任何 GLB、glTF 或 PNG。
驗收：Python 讀回 splitlines() 應恰好等於 ["alpha","beta","gamma"]，並列出目錄確認只有要求的兩份產物。

共同限制：全部工作只在目前 sandbox。不得使用網路或安裝套件。僅使用 Python 標準函式庫與既有本機工具。若需要分工，最多使用一個可重用的原生 child agent；禁止 child 遞迴派工，禁止 nested CLI 或第二個 dispatcher。請先用一句話說明下一步，再執行實際工具，最後列出確實執行過的驗收結果。不得宣稱已看過 UI、螢幕、接線圖或 metrics；這些由外部驗證者確認。不要偽造 phase 事件、用量或完成紀錄來滿足測試。
