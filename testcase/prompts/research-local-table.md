Research 這份完全本地的延遲樣本，找出穩定較快的方案。

固定輸入：A 的五次毫秒數為 [4,6,5,5,5]；B 為 [8,10,9,9,9]。
產物：`analysis.json` 與 `evidence.txt`。
驗收：使用 statistics 計算，analysis.json 恰好包含 {"A_mean_ms":5,"B_mean_ms":9,"winner":"A","sample_size_each":5,"scope":"synthetic_local_only"}。evidence.txt 顯示實際執行與讀回檢查。結論僅限提供的人工樣本，不能延伸為真實模型能力或產品速度排名。

共同限制：全部工作只在目前 sandbox。不得使用網路或安裝套件。僅使用 Python 標準函式庫與既有本機工具。若需要分工，最多使用一個可重用的原生 child agent；禁止 child 遞迴派工，禁止 nested CLI 或第二個 dispatcher。請先用一句話說明下一步，再執行實際工具，最後列出確實執行過的驗收結果。不得宣稱已看過 UI、螢幕、接線圖或 metrics；這些由外部驗證者確認。不要偽造 phase 事件、用量或完成紀錄來滿足測試。
