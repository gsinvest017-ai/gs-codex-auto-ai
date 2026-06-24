# CodexAutoAI — 多 Agent 自動開發系統

Claude Code 擔任調用中心，協調 sub-agent 與 OpenAI Codex CLI 完成開發任務。

---

## 事前準備（首次使用必讀）

使用本系統前，需要完成以下兩個工具的登入：



### 1. Claude Code 登入

```bash
# 確認已安裝
claude --version

# 登入（會開啟瀏覽器）
claude login
```



### 2. OpenAI Codex CLI 登入

```bash
# 確認已安裝（如未安裝）
npm install -g @openai/codex

# 確認版本
codex --version

# 登入（會開啟瀏覽器）
codex login
```
