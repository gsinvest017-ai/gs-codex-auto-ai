# Delta for Safety

> OS 沙箱、allow/deny、事後稽核、單一 interrupt gate、動態路徑。
> 修補 REVIEW5 P1-⑥ P1-⑦。憲章對映 C6 C9。
> 對齊使用者需求「不要按按鈕批准」——以沙箱 + 事後稽核安全達成。

## ADDED Requirements

### Requirement: SAFE-R1 — OS 沙箱執行
WHEN a builder or Codex process executes, THE SYSTEM SHALL confine it within an OS-enforced sandbox bounding filesystem access to its worktree and network access to an allow-list; WHERE OS sandboxing is unavailable (e.g. Windows without support) THE SYSTEM SHALL fall back to a deny-list plus post-hoc audit. [來源 Claude Code / OpenHands；fixes P1-⑥]

#### Scenario: SAFE-R1-S1 — 沙箱內自由跑免逐次批准
- GIVEN 一個在沙箱內執行的 builder
- WHEN 它執行 worktree 內的檔案讀寫與 allow-list 內的指令
- THEN SHALL 不逐次請求批准（消除 approval fatigue）

#### Scenario: SAFE-R1-S2 — 越界被擋
- GIVEN sandboxed builder 嘗試寫入 worktree 以外的路徑
- WHEN 寫入發生
- THEN OS 邊界 SHALL 阻擋該寫入並記錄事件

### Requirement: SAFE-R2 — 不可逆操作的單一 Gate
THE SYSTEM SHALL deny-list irreversible operations (commit, push, deploy, delete, overwrite outside worktree) and SHALL require exactly one human interrupt gate before any of them executes. [憲章 C6；fixes P1-⑥]

#### Scenario: SAFE-R2-S1 — push 前必經 gate
- GIVEN 流水線完成、準備 push
- WHEN 到達不可逆邊界
- THEN SHALL 暫停於單一 interrupt gate 等待人類核可，其餘 routine 操作全程不暫停

### Requirement: SAFE-R3 — 事後稽核取代事前批准
THE SYSTEM SHALL make routine sandboxed actions auditable after the fact via the event trace rather than gating each action before execution.

#### Scenario: SAFE-R3-S1 — 事後可稽核
- GIVEN 一連串 routine 沙箱操作已執行
- WHEN 人類要審查
- THEN SHALL 能從事件 trace 完整回看每個操作，無需事前逐一批准

### Requirement: SAFE-R4 — 動態環境解析
THE SYSTEM SHALL resolve toolchain paths (uv, python, ports) dynamically at runtime, and SHALL NOT hard-code user-specific absolute paths. [fixes P1-⑦；憲章 C9]

#### Scenario: SAFE-R4-S1 — 跨機器可移植
- GIVEN 在一台帳號名不是 `User` 的機器上執行
- WHEN 系統需要 uv/python
- THEN SHALL 以 `command -v` / 環境變數動態解析，不依賴 `/c/Users/User/...` 之類硬編路徑
