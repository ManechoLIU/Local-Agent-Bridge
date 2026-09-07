# Local Agent Bridge / Computer Runtime — Runtime Initialization Checkpoint

更新时间：2026-09-07（Asia/Singapore）

## 本轮范围

本轮仅完成 Adaptive Agent Runtime 最小开发治理初始化、已有 PoC 工作收编与初始化缺陷闭环；没有开始完整 Local Agent Bridge / Computer Runtime 产品开发，没有修改 SelfAlone main，也没有接管或复用 SelfAlone Controller。

## Canonical project facts

- 正式项目根：`/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge`
- Runtime project identity boundary：`/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge/.git`（Adaptive Agent Runtime 当前以 Git common-dir 作为项目身份边界；没有额外 project_id UUID 字段）
- Git branch：`main`
- Git HEAD：`513ad89503adfdd68dba1f4ab9e4d8994b47fdb7`（本轮 fresh-read；后续以 `git rev-parse HEAD` 为准）
- 唯一 logical Controller ID：`a697051d-402b-4062-a883-a29ba8b395e9`
- 当前用户授权 Web execution target：`6a9e4158-cc8c-83ea-9763-05a7fd51f597`
- Bootstrap binding：`provenance=manual_user_authorized`、`binding=temporary`、`host_attested=false`、Runtime lease `mode=resume_only`
- 重要语义：Runtime identity CLI 对当前唯一 Web alias 会显示 `VERIFIED / BOUND_REGISTERED_WEB_SESSION / legacy_single_binding`；这只表示 registry binding 可解析，**不等于 host-attested Web identity**，不得把它描述成强身份绑定成功。

## Runtime revision / handshake

- installed Runtime revision：`e639d0656d8161f02f00aace6f6d7daa374e349a`
- previous revision：`1ad9193bc1a9df4db36b006f4065bc63fd492535`
- installed fix：支持没有首个 commit 的 unborn `main` 项目完成 lifecycle/project snapshot，不伪造 HEAD revision
- 定向回归已通过：
  - unborn project snapshot PASS
  - unborn Web restore payload PASS
  - runtime liveness 邻近回归 PASS
  - retained candidate 邻近回归 PASS
- 当前 rule ACK：已加载并 ACK exact installed revision
- 当前 handshake：`pending_live_e2e` / `blocking=true`
- 原因：该 revision 修改了 lifecycle / Web bridge 控制路径，Runtime 要求真实 `confirmed wake -> clean CLOSED controller cycle -> accept-live-e2e`。当前仅有 manual/non-host-attested Web binding，不能合法伪造 confirmed wake，因此保持 fail closed。

## Canonical governance assets

Runtime collaborative 初始化器已建立并复用：

- `AGENTS.md`（原有内容保留并补齐当前 Runtime registration / Bootstrap binding）
- `TASK_LEDGER.md`（唯一任务台账）
- `SPEC.md`
- `DESIGN.md`
- `TECHNICAL.md`
- `EVOLUTION.md`

没有创建第二套 PROJECT_STATUS / assignment ledger / reviewer ledger。

## 已收编工作状态

### DONE

- `LAB-GOV-01`：项目 Runtime 最小治理初始化、唯一 Controller、temporary Web binding、unborn 初始化缺陷修复与安装
- `LAB-BASE-01`：既有 CatDesk / chatgpt-local-coder PoC 与旧 checkpoint 调查结果收编
- `LAB-ID-01`：最小只读 `identity_probe` 已实现；`node --test experiments/identity-probe/server.test.mjs` 为 4 PASS / 0 FAIL

### READY

- `LAB-ID-02`：真实 ChatGPT Web -> identity_probe 身份信号 E2E；这是初始化后的下一条产品关键路径，本轮按用户边界不启动

### BLOCKED

- `LAB-GOV-02`：Runtime continuation Live-E2E，等待可信 Web execution-target / host-session attestation 以产生真实 confirmed wake
- `LAB-SAFE-01`：workspace write/move/hardlink、Shell sandbox 与 capability 安全闭环，依赖 `LAB-ID-02`
- `LAB-COMP-01`：独立 Computer Runtime 最小 API / 授权 / Bridge E2E，依赖身份与安全边界

### ACTIVE / WIP

- 无。
- `assignment_runtime.load_runtime_state()` 当前为 `leases={}`、`lineages={}`。
- 未发现 non-terminal reviewer run。

## Lifecycle / recovery state

- 当前 lifecycle `pending_control_event=true`，`requires_user=false`。
- 当前 snapshot 能正确处理 unborn repo：`head=null`、`candidate_revisions=[]`、`ready_ids=[LAB-ID-02]`。
- 当前 wake receipt 为失败/待闭合：旧 native resume 路径尝试把 logical Controller ID 当 desktop Codex rollout/thread 恢复目标，宿主返回 `no rollout found`；因此没有把失败冒充成功，也没有创建第二 Controller。
- lifecycle 中仍保留早期模板阶段的 continuation debt 标识 `runnable:INIT-01`，但当前 ledger 已无 `INIT-01`，且没有 runtime lease；以当前 ledger/snapshot 为权威。后续真实控制闭环应让 Runtime 自行重算/清理该瞬态 debt，不另建状态文件手工覆盖。

## 既有 PoC 资产

- CatDesk PoC：`/Users/echoman/CatDesk-PoC-20260907`
- CatDesk checkout：`/Users/echoman/CatDesk-PoC-20260907/src/CatDesk`
- chatgpt-local-coder checkout：`/Users/echoman/CatDesk-PoC-20260907/src/chatgpt-local-coder`
- 已确认历史风险仍有效：CatDesk URL-as-secret 不能承担 execution identity；hardlink read escape 已复现；macOS shell 无 workspace OS sandbox；现有 Browser 能力不等于独立 Computer Runtime。
- 不重新克隆上述有效资产；只有事实失效或测试需要时才复验。

## 下一条真实关键路径

在不扩大 Files/Shell/Browser/Computer 权限面的前提下执行 `LAB-ID-02`：

`ChatGPT Web -> minimal identity_probe -> sanitized headers / MCP _meta / OAuth/client facts -> attestation correlation decision`

目标是明确哪些信号可以作为机器验证输入、哪些只是相关性。只有形成可信 execution-session / host-session attestation 后，才继续 `LAB-GOV-02` 的真实 continuation Live-E2E，并最终淘汰 `manual_user_authorized` Bootstrap binding。

## Current Web target reconciliation

- 用户当前明确授权 Web execution entry：`6a9e4158-cc8c-83ea-9763-05a7fd51f597`
- prior alias `6a9e4037-0a00-83ea-bb8e-1301c5f6b979`：historical only
- Runtime lineage：新 session 已登记到同一 logical Controller
- Runtime machine projection：`UNVERIFIED / historical_alias / STALE / EXPLICIT_CURRENT_TARGET_REQUIRED`
- `controller_actions_allowed=false`，`host_attested=false`
- 旧 manual lease 尚指向 prior alias；canonical authorize/rotation 被新 Runtime 门禁拒绝，因为没有 explicit verified current Web target
- 因此不得把本次登记描述成 host-attested/current-target 强绑定成功；后续需要 Runtime 提供 Web target replace/unbind/verified rotation 能力，或由新 Bridge 提供可信 host attestation。


## Product architecture boundary added during PoC

- Local Tool Bridge / Computer Runtime 必须保持 model-agnostic；不得硬编码 ChatGPT Web 模型调用、模型路由或 Agent lifecycle。
- 未来 Web ChatGPT Executor 是独立 adapter / executor plane，由 Adaptive Agent Runtime 路由；需要本机能力时再调用 Local Tool Bridge。
- 推荐边界：`Adaptive Agent Runtime -> Web ChatGPT Executor -> Personal ChatGPT Web`；本机工具路径为 `Web ChatGPT Executor -> Local Tool Bridge -> ... -> Computer Runtime / local machine`。
- Web conversation 与 Bridge execution session 的关联由外部 adapter/orchestration 层持有；Bridge 只报告其实际可验证的 transport/session/capability facts。

## Current LAB-ID-02 PoC checkpoint

- Plus Personal 当前真实 UI 已验证 `Plugins -> Create app` 存在。
- New Plugin 表单已验证 Connection 支持 `Server URL / Tunnel`，Authentication 支持 `OAuth / No Auth / Mixed`。
- 专用只读 `identity_probe` 正在 `127.0.0.1:33302` 运行；临时 localhost.run TLS ingress 的公网 `server/discover` 已 PASS。
- 旧 `experiments/web-local-poc/proxy.mjs -> CatDesk` 链路不作为本轮 identity Web ingress，因为它会扩大到 CatDesk 完整工具面；本轮先只暴露只读 identity probe。
- 当前唯一 UI 阻点：AI-Bridge browser 无法可靠修改原生 Authentication `<select>`；待值切为 `No Auth` 后可继续 Create -> real Web tool call。
- N6 PREP：`experiments/web-local-poc/safe-poc-server.mjs` 已本地真实 PASS read/write/edit/fixed harmless command/background job start+poll；固定 workspace、固定动作，不接受任意路径或任意 shell；仍不得冒充 Web E2E。


## LAB-ID-02 final PoC result — CONDITIONAL-GO

第一轮真实 Personal ChatGPT Plus Web PoC 已完成，结论为 **CONDITIONAL-GO**：核心产品路径成立，没有发现平台级不可行 blocker；剩余问题属于安全化、能力泛化、产品化与 Computer Runtime 实现。

真实通过链路：

`ChatGPT Plus Personal -> Custom Plugin -> OpenAI Secure MCP Tunnel -> Local Bridge PoC -> isolated local workspace`

本轮关键事实：

- 在 Plus Personal 真实 UI 中成功创建并连接 `Local Bridge Identity Probe` 自定义 Plugin。
- 使用独立 tunnel `tunnel_6a9e90a02f548191b3f2458617513158`，没有复用旧 AI-Bridge tunnel 作为本项目产品路径。
- OpenAI `tunnel-client` runtime 对本项目 tunnel 报 `healthy=true`、`ready=true`，MCP session 初始化成功。
- 为兼容当前 ChatGPT connector contract，`identity_probe` 已支持 OpenAI `server/discover` 的 `resultType/supportedVersions/capabilities/ttlMs/cacheScope/_meta.serverInfo` 结构，以及 `tools/list` 的 `resultType/ttlMs/cacheScope/tools` 结构。
- 首次真实 Web `identity_probe` tool call 成功。
- 随后通过同一 Plugin Refresh 更新安全 PoC tool inventory，并由普通 Personal Web 测试 chat 真实按顺序调用：`poc_read`、`poc_write`、`poc_edit`、`poc_command`、`poc_start_job`、`poc_poll_job`。
- 本机固定隔离 workspace 最终 `write-target.txt` 内容为 `edited-by-chatgpt-web-poc`；固定 `/bin/pwd` command 返回隔离 workspace；后台 job 最终 finished。
- 所有 PoC side effects 都限制在固定 test workspace / fixed command / fixed short job，不代表通用 Files/Shell 已安全上线。

身份观测结论：

- 真实 OpenAI tunnel 请求可观察到脱敏后的 `x-openai-session`、`x-openai-subject`、`x-request-id`、trace 与 forwarded-client-cert 类信号。
- 同一 Web executor chat 的 `x-openai-session` 指纹在 read/write/edit/command/job start/poll 六次调用间稳定。
- `x-openai-subject` 指纹在两个不同 Personal Web 测试 chat 间保持稳定。
- 目前没有观察到可直接等同于 ChatGPT conversation ID 的机器字段；这些 header 只能先作为 transport/session/principal correlation facts，不能直接升级为 Adaptive Agent Runtime Controller host attestation。

因此下一条真实关键路径切换为 `LAB-SAFE-01`：Auth -> Execution Session -> Capability -> Authorization -> Workspace/Shell isolation。Local Tool Bridge / Computer Runtime 继续保持 model-agnostic；Web ChatGPT Executor 仍属于未来独立 executor plane。


## LAB-SAFE-01 security boundary progress

- `experiments/security-boundary` Rust Harness 已扩到 16/16 PASS。
- Authorization prototype 明确绑定 `principal_id / execution_session_id / capability_id / workspace_id`；不引用 ChatGPT model、Web conversation lifecycle、Runtime Controller/Goal/DAG。
- Capability authority 新增 TTL、revocation、session-generation fencing、request nonce replay protection；authority key/state 0600 原子持久化，重启后 replay/revocation 仍保持。
- Local ingress proof 使用 per-capability HMAC 逐请求绑定 principal/session/workspace、request ID/nonce、MCP method/tool 与 body hash；proof 可映射到 `x-lab-*` ingress headers，但 signing secret 不出本机 signer。
- `x-openai-session` / `x-openai-subject` 明确留在 correlation-only 层：Harness 已验证它们不能单独作为 authorization source。
- Unix Files prototype 使用 directory FD + `openat/O_NOFOLLOW/fstat/fstatat/renameat`，已闭合 hardlink read/write、symlink existing write、nonexistent target under symlink parent、traversal 与 secure move 的当前测试门。
- macOS Shell prototype 使用 replaceable `sandbox-exec` backend：workspace I/O PASS；敏感路径外读、workspace 外写、outbound network 均阻断；继承环境清空。`sandbox-exec` 不冻结为最终产品依赖。
- CatDesk 独立 worktree `/Users/echoman/CatDesk-PoC-20260907/worktrees/lab-safe-01` 先以真实 upstream 行为得到 RED：hardlink read、nonexistent write/move symlink-parent、macOS shell outside-workspace 全部失败安全门；candidate patch 后 8 个新增 security tests 全部 PASS；`cargo test security_` 为 9/9（含一个既有 connector security-name test）。
- CatDesk candidate full suite 为 216 PASS / 6 FAIL；六个 FAIL 都属于 `change_tracking`，在未修改 upstream `bfac47b` 上已复现，因此不是本 security candidate 引入。
- 下一步仍是 `LAB-SAFE-01`：把当前本机 contract 接入正式 Bridge ingress，补 capability issuance / expiry / revocation / replay protection，并在扩大通用 Files/Shell 前继续 fail closed。
