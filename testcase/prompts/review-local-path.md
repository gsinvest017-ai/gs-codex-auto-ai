Review 下列本地路徑限制是否成立，只給 findings，不修改待查片段。

```python
from pathlib import Path
def load(root, supplied):
    return (Path(root) / supplied).read_text()
```
產物：`review.json` 與 `evidence.txt`。
驗收：在 sandbox 內建立 parent/a 與 parent/b 兩個小資料夾，將 root 設為 parent/a，證明 supplied='../b/sample.txt' 會越過預期 root 但仍留在 sandbox。review.json 使用 findings 陣列，至少含一項 id='path_escape'、evidence 與建議以 resolve 後檢查 is_relative_to 的修正方向。不得讀取 sandbox 外的真正敏感檔。

共同限制：全部工作只在目前 sandbox。不得使用網路或安裝套件。僅使用 Python 標準函式庫與既有本機工具。若需要分工，最多使用一個可重用的原生 child agent；禁止 child 遞迴派工，禁止 nested CLI 或第二個 dispatcher。請先用一句話說明下一步，再執行實際工具，最後列出確實執行過的驗收結果。不得宣稱已看過 UI、螢幕、接線圖或 metrics；這些由外部驗證者確認。不要偽造 phase 事件、用量或完成紀錄來滿足測試。
