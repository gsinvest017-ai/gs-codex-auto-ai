Review 這份 3D mesh 規格的索引範圍，重點是審查而非建立模型。

固定資料：vertex_count=3，indices=[0,1,3]。
產物：`review.json`、`evidence.txt`。
驗收：用 Python 查出 index 3 越界，合法範圍為 0..2。review.json 必須是 {"valid":false,"invalid_indices":[3],"expected_range":[0,2]}。不能建立 GLB 或 PNG。evidence.txt 記錄實際讀回結果。

共同限制：全部工作只在目前 sandbox。不得使用網路或安裝套件。僅使用 Python 標準函式庫與既有本機工具。若需要分工，最多使用一個可重用的原生 child agent；禁止 child 遞迴派工，禁止 nested CLI 或第二個 dispatcher。請先用一句話說明下一步，再執行實際工具，最後列出確實執行過的驗收結果。不得宣稱已看過 UI、螢幕、接線圖或 metrics；這些由外部驗證者確認。不要偽造 phase 事件、用量或完成紀錄來滿足測試。
