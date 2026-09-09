Implement 兩個純函式完成攝氏與華氏轉換。

產物：`src/temperature.py`、`tests/test_temperature.py`、`evidence.txt`。
函式 c_to_f(x) = x*9/5+32；f_to_c(x) = (x-32)*5/9。只接受數值輸入，這次不需要 CLI 或型別驗證擴充。
驗收：unittest 至少檢查 0°C→32°F、100°C→212°F、-40°C→-40°F、32°F→0°C、212°F→100°C。使用 assertAlmostEqual。實際執行測試並留下通過數。

共同限制：全部工作只在目前 sandbox。不得使用網路或安裝套件。僅使用 Python 標準函式庫與既有本機工具。若需要分工，最多使用一個可重用的原生 child agent；禁止 child 遞迴派工，禁止 nested CLI 或第二個 dispatcher。請先用一句話說明下一步，再執行實際工具，最後列出確實執行過的驗收結果。不得宣稱已看過 UI、螢幕、接線圖或 metrics；這些由外部驗證者確認。不要偽造 phase 事件、用量或完成紀錄來滿足測試。
