// prompt.js — 把使用者需求清成「可安全送進終端機的純文字」。
//
// extension 的「啟動」把需求交給 `terminal.sendText(\`claude "<需求>"\`)`，
// 那是**直接餵給一個活的 shell**（PowerShell / cmd / bash），所以含
// `$(...)` / `` `...` `` / `&` 的需求會被當成指令執行。原本只把 `"` 換成 `'`
// 完全擋不住這些。
//
// 這是 desktop/launcher.py 的 `_safe_prompt` 的 JS 對應版，**兩邊規則必須一致**
// ——同一句需求從 App 或從 VS Code 送出去，結果不該不同。改一邊就要改另一邊，
// 對應測試：tests/test_launcher.py 與本檔的 self-check。
//
// 純語法字元刪掉；在中文 prose 裡有意義的字元（% ! & $ ; < >）轉成全形等價字，
// 語意保留但對 shell 失去意義。中文與全形標號（（）「」）完全不動。
const META_MAP = {
  '"': "", "'": "", "`": "", "|": "", "^": "", "\\": "",   // 純語法，刪除
  "$": "＄", "&": "＆", ";": "；", "<": "＜", ">": "＞",     // prose 常用，轉全形
  "%": "％", "!": "！",
  "\n": " ", "\r": " ", "\t": " ",                          // 命令列不能有換行
};

function safePrompt(text) {
  const mapped = Array.from(String(text || ""))
    .map((ch) => (ch in META_MAP ? META_MAP[ch] : ch))
    .join("");
  return mapped.split(/\s+/).filter(Boolean).join(" ");
}

module.exports = { safePrompt, META_MAP };
