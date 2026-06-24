# Delta for Parallel-Build

> worktree 隔離、所有權切分、coordinator 合併、動態資源、編輯守門。
> 修補 REVIEW5 P0-④ P1-⑧。憲章對映 C7 C9。

## ADDED Requirements

### Requirement: BUILD-R1 — Worktree 隔離
WHEN multiple builders run in parallel, THE SYSTEM SHALL give each builder its own git worktree with a private HEAD, index, and working tree; IF the project is not a git repo THEN THE SYSTEM SHALL initialize one or fall back to per-file ownership locks. [來源 git worktree / Anthropic；fixes P0-④]

#### Scenario: BUILD-R1-S1 — 並行寫入互不可見
- GIVEN builder-A 與 builder-B 並行
- WHEN 兩者各自寫檔
- THEN A 的編輯在 B 的 worktree 不可見，消滅 lost-update；衝突延後到 merge 時偵測

### Requirement: BUILD-R2 — 檔案所有權切分
THE SYSTEM SHALL partition build tasks by file ownership such that no two parallel builders own the same file; IF multiple FNs target the same file THEN THE SYSTEM SHALL serialize them under one builder. [來源 Anthropic 所有權切分；fixes P0-④ 根因]

#### Scenario: BUILD-R2-S1 — 同檔 FN 序列化
- GIVEN FN-1 與 FN-2 都寫 `src/utils.py`
- WHEN orchestrator 分批
- THEN 兩者 SHALL 由同一 builder 序列處理，不得分配給兩個並行 builder

### Requirement: BUILD-R3 — Coordinator 合併
WHEN a parallel build batch completes, THE SYSTEM SHALL merge each worktree back via 3-way merge through a merge-coordinator, and IF a merge conflict arises THEN THE SYSTEM SHALL report it rather than silently overwrite.

#### Scenario: BUILD-R3-S1 — 衝突浮現而非覆蓋
- GIVEN 兩個 worktree 改動了重疊區域
- WHEN merge-coordinator 合併
- THEN SHALL 以 3-way merge 偵測衝突並回報，禁止後寫覆蓋前寫

### Requirement: BUILD-R4 — 動態共享資源
WHEN builders or test-runners need a server port or database, THE SYSTEM SHALL assign a per-worktree port offset and a unique database name, and SHALL use health-check polling instead of fixed sleeps to detect readiness. [fixes P1-⑧]

#### Scenario: BUILD-R4-S1 — 並行測試不撞 port
- GIVEN 兩個 test-runner 並行各需啟動 server
- WHEN 它們啟動
- THEN 各自綁不同 port（如 :8001/:8002），且以輪詢 health endpoint 確認就緒，不用 `sleep 3`

### Requirement: BUILD-R5 — 編輯語法守門
WHEN a builder writes or edits a source file, THE SYSTEM SHALL reject any edit that makes the file fail to parse, returning the precise syntax error and nearby lines. [來源 SWE-agent ACI lint guard]

#### Scenario: BUILD-R5-S1 — 破壞 parseable 的編輯被拒
- GIVEN 一次會造成語法錯誤的編輯
- WHEN builder 嘗試寫入
- THEN SHALL 拒絕該寫入並回傳精確錯誤位置，要求重出
