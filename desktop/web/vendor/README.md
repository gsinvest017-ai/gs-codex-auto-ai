# Vendored 前端資產

這些檔案是**建置時就放進版控的第三方資產**，不是執行期下載的。

原因：Artifact / webview 的 CSP 禁止連外部主機，而且桌面 App 必須離線可用——
不能在執行期抓 CDN。

| 檔案 | 來源 | 版本 | 授權 |
|---|---|---|---|
| `xterm.js` / `xterm.css` | [`@xterm/xterm`](https://www.npmjs.com/package/@xterm/xterm) | 6.0.0 | MIT（見 `LICENSE.xterm`）|
| `addon-fit.js` | [`@xterm/addon-fit`](https://www.npmjs.com/package/@xterm/addon-fit) | 0.11.0 | MIT |

更新方式：

```bash
npm pack @xterm/xterm @xterm/addon-fit
# 解開後把 lib/xterm.js、css/xterm.css、lib/addon-fit.js 覆蓋過來，並更新上表版本
```

**不要手改這些檔案**——它們是上游產物，改了下次更新就沒了。
