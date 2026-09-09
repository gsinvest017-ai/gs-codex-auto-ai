Debug 一個漏掉首項的整數加總函式。

輸入基線如下，先將它存為 src/total.py：
```python
def total(values):
    answer = 0
    for i in range(1, len(values)):
        answer += values[i]
    return answer
```
產物：修正後的 `src/total.py`、`tests/test_total.py`、`evidence.txt`。
驗收：unittest 至少包含 []→0、[7]→7、[1,2,3]→6、[-2,5]→3。先實際跑基線，記錄失敗，再修改最小必要內容並跑同一組測試成功。evidence.txt 記錄失敗原因及最後測試數。

共同限制：全部工作只在目前 sandbox。不得使用網路或安裝套件。僅使用 Python 標準函式庫與既有本機工具。若需要分工，最多使用一個可重用的原生 child agent；禁止 child 遞迴派工，禁止 nested CLI 或第二個 dispatcher。請先用一句話說明下一步，再執行實際工具，最後列出確實執行過的驗收結果。不得宣稱已看過 UI、螢幕、接線圖或 metrics；這些由外部驗證者確認。不要偽造 phase 事件、用量或完成紀錄來滿足測試。
