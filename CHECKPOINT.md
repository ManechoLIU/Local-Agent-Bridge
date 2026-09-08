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

- `experiments/security-boundary` Rust Harness 已扩到 20/20 PASS。
- Authorization prototype 明确绑定 `principal_id / execution_session_id / capability_id / workspace_id`；不引用 ChatGPT model、Web conversation lifecycle、Runtime Controller/Goal/DAG。
- Capability authority 新增 TTL、revocation、session-generation fencing、request nonce replay protection；authority key/state 0600 原子持久化，重启后 replay/revocation 仍保持。
- Operational hardening 已补：master key 使用 versioned generation，rotation 采用“先落新 key、再原子提交 state pointer”的 crash-safe 顺序并带 expected-generation fence；state dir 0700、key/state 0600，symlink state path fail closed。
- Local Tool Bridge 现在在任何 backend side effect 之前把 provider `x-request-id`/nonce replay state 持久化；同 provider request 重放在进程内与重启后都被 403 拒绝。ingress tests 8/8 PASS；真实进程/UDS hardening tests 3/3 PASS。
- 第一次真实 Grok 4.6 xhigh Security Reviewer 调用确实启动并读取 immutable `d512734..0756cfa`，但宿主执行超时后没有得到最终 JSON verdict，因此该 review attempt 标记 `result_unknown`，没有冒充 Reviewer PASS，也不把它作为 SAFE-01 closure evidence。
- Local ingress proof 使用 per-capability HMAC 逐请求绑定 principal/session/workspace、request ID/nonce、MCP method/tool 与 body hash；proof 可映射到 `x-lab-*` ingress headers，但 signing secret 不出本机 signer。
- `x-openai-session` / `x-openai-subject` 明确留在 correlation-only 层：Harness 已验证它们不能单独作为 authorization source。
- Unix Files prototype 使用 directory FD + `openat/O_NOFOLLOW/fstat/fstatat/renameat`，已闭合 hardlink read/write、symlink existing write、nonexistent target under symlink parent、traversal 与 secure move 的当前测试门。
- macOS Shell prototype 使用 replaceable `sandbox-exec` backend：workspace I/O PASS；敏感路径外读、workspace 外写、outbound network 均阻断；继承环境清空。`sandbox-exec` 不冻结为最终产品依赖。
- CatDesk 独立 worktree `/Users/echoman/CatDesk-PoC-20260907/worktrees/lab-safe-01` 先以真实 upstream 行为得到 RED：hardlink read、nonexistent write/move symlink-parent、macOS shell outside-workspace 全部失败安全门；candidate patch 后 8 个新增 security tests 全部 PASS；`cargo test security_` 为 9/9（含一个既有 connector security-name test）。
- CatDesk candidate full suite 为 216 PASS / 6 FAIL；六个 FAIL 都属于 `change_tracking`，在未修改 upstream `bfac47b` 上已复现，因此不是本 security candidate 引入。
- 下一步仍是 `LAB-SAFE-01`：把当前本机 contract 接入正式 Bridge ingress，补 capability issuance / expiry / revocation / replay protection，并在扩大通用 Files/Shell 前继续 fail closed。

## LAB-SAFE-01 local ingress candidate

- 已把安全核心从单纯 experiment 提升为 `crates/bridge-security-core` 生产候选 crate；`experiments/security-boundary` 改为对该 crate 的外部 Harness，保持 16/16 PASS。
- 新增 `crates/local-tool-bridge`：本地 ingress 只允许 loopback backend；要求独立 local ingress key；按 MCP method/tool 映射 capability；成功后生成逐请求 HMAC `x-lab-*` proof 并转发到本地 backend。
- local ingress proof 绑定 principal/session/workspace/capability、request id/nonce、MCP method/tool、body hash 和 provenance；ingress key 与 capability signing secret 均不转发给 backend。
- `x-openai-subject` 等 correlation headers 不作为本地授权，也不被 ingress 转发成 trusted proof。
- ingress tests 4/4 PASS：认证+signed proof、correlation-only 不可替代 local key、缺 capability 在 backend 前 fail closed、backend 必须 loopback。
- 下一步：真实连接 `tunnel-client -> local-tool-bridge ingress -> CatDesk security candidate`，再验证 read/write/edit/shell/job 的 scoped capability E2E 与 revoke/replay 行为。

## Signed Local Tool Bridge ingress E2E

- `crates/local-tool-bridge` 已新增独立 0600 Unix-domain socket runtime，UDS 文件权限作为本机 transport boundary；backend 仍强制 loopback-only。
- UDS ingress 在转发前按 method/tool capability map 授权，并注入逐请求 signed `x-lab-*` proof；本地 ingress secret 不向 backend/OpenAI correlation headers 泄露。
- 真实链路已跑通：`curl over UDS -> local-tool-bridge -> signed proof -> CatDesk lab/security-boundary candidate -> workspace/macOS sandbox`。
- 实测完成 `server/discover`、`tools/list`、`catdesk_instruction`、read、write、edit、`/bin/pwd`、background `start_command -> poll_command`。write/edit 最终内容为 `edited-through-signed-ingress`，job 输出 `job-secure-done`。
- 首次 shell E2E 暴露 `.catdesk-sandbox` scratch 会污染 workspace/change tracking；CatDesk candidate 已新增 commit `707b95e`，把 sandbox HOME/TMP scratch 移到 `~/.catdesk/sandbox` 并显式授权该 scratch，复跑后 workspace 只剩业务文件。
- 下一步是把 OpenAI Secure MCP `tunnel-client` 的 local target 从 TCP/旧 probe 切到该 UDS ingress，证明 `Personal ChatGPT Web -> Secure MCP Tunnel -> UDS Local Tool Bridge -> hardened CatDesk -> Mac` 全链。

## LAB-SAFE-01 Personal Plus full secure-chain E2E

- OpenAI Secure MCP Tunnel 已从旧 identity probe TCP target 切到 direct Unix-socket target：`url=http://localhost/mcp` + `unix_socket=/tmp/lab-real-ingress-e2e/bridge.sock`。tunnel-client startup 机器日志记录 `mcp channel route resolved ... unix_socket_path=...`，随后 `mcp session initialized protocol=2026-07-28 server=catdesk version=4.0.0`，health/ready 均 PASS。
- ChatGPT Plugins 中对现有 `Local Bridge Identity Probe` 执行 Refresh，tunnel-client 收到真实 `openai-mcp-discover` / `tools/list` control-plane command，CatDesk 返回 10 个当前工具。
- 新建普通 Personal Chat（非 Work），真实 Web model 只使用该 Plugin，按顺序调用 `read -> write -> edit -> run_command -> start_command -> poll_command`。
- 本机 workspace 证据：`read-test.txt=hello-from-secure-catdesk`；`web-secure-target.txt=edited-by-personal-web-through-signed-bridge`；`/bin/pwd` 返回隔离 workspace。
- Web start job 为 `sleep 1; printf web-secure-job-done`；真实 Web poll 后，又从 UDS Local Tool Bridge 本地复核同一 job `8bf9e70b-df92-464a-845a-57ef66cccc79`：`state=succeeded`、`success=true`、`commandSuccess=true`、`exitCode=0`、stdout=`web-secure-job-done`。
- 因此 `Personal ChatGPT Plus -> OpenAI Secure MCP Tunnel -> direct Unix socket -> signed Local Tool Bridge -> hardened CatDesk -> macOS workspace/sandbox` 的 scoped general Files/Shell/Jobs 全链已经真实通过。
- 该结果不改变身份诚实性：`x-openai-session`/`x-openai-subject` 仍只是 correlation facts，不是 Runtime Controller/Web conversation host attestation。剩余 SAFE-01 工作是独立 Security Review、key rotation/crash recovery、same-UID hostile-process 威胁边界和 packaging。

## LAB-SAFE-01 recovery + v1 threat decision

- 新增真实进程恢复测试 `crates/local-tool-bridge/tests/runtime_recovery.rs`：启动真实 `local-tool-bridge` binary，经 UDS 发固定 provider request-id，首次 200；kill 后以 master-key rotation 重启；旧 request-id 必须 403，fresh request-id 必须 200；持久状态必须为 key generation 2；rotation 后旧 generation key 清理、当前 key.2 保留；UDS=0600、socket parent=0700、state dir=0700。另加 symlink socket-parent fail-closed 与 restart grant-mismatch fail-closed，合计 3/3 PASS。
- v1 安全范围已明确：防 remote/cloud/prompt-originated、其他 OS 用户、capability replay/expiry/revocation、workspace/File/Shell escape；不宣称防御已运行在同一 macOS UID 下的恶意本地进程。
- 当前 CatDesk loopback backend 因此是 migration backend；same-UID adversary 可绕过 Bridge 直接访问它这一点作为已披露限制保留。若未来需要 same-UID isolation，应直接内建 backend 或使用独立 OS principal/受认证 worker。

## LAB-SAFE-01 Grok FINDINGS remediation

- 第二次真实 Grok 4.6 xhigh Security Reviewer 已对 immutable `8fd6f6c` 完整返回 `FINDINGS`：1 Critical / 5 Important / 5 Minor。
- Critical（UDS parent/peer trust）已修：Local Tool Bridge 在 bind 前验证 socket parent 为真实目录、owner=current euid，并收紧 0700；existing socket 必须是当前 euid 拥有的 Unix socket；macOS `getpeereid` / Linux `SO_PEERCRED` 验证 peer UID；socket 0600。
- Backend important 已修：backend URL 只接受 numeric loopback IP、拒绝 `localhost`/credentials/query/fragment；reqwest redirect policy=none；Bridge→CatDesk 增加独立 0600 backend key。实测 CatDesk direct loopback 无 key=401，正确 key=200。CatDesk candidate commit=`8206454`。
- Authority crash/replay important 已修：已有 key material 但 state 缺失时 fail-closed；state/key owner+permission/symlink 检查；request-id/nonce 在 backend side effect 前持久化；重启后 replay 仍拒绝。
- Capability integrity important 已修：grant capability set 进入派生 signing secret；同 capability ID restart 时 grant 与当前配置不一致直接拒绝；`resources/read` 明确要求 `FileRead`。
- macOS sandbox important 已收紧：去掉 broad `(allow process*)`、`mach-lookup`、`ipc-posix-shm`，仅保留 process exec/fork；security tests 保持 GREEN；macOS harness 不再因 `sandbox-exec` 缺失而静默成功。
- Rotation/minor 已修：key.N+1 orphan 可在 crash retry 复用，state commit 后清理 previous key；shared-key HTTP ingress 也要求 state_dir 并持久化 replay；backend hostname `localhost` 被拒绝；redirect 不跟随。
- 当前验证：security boundary 19/19 PASS；Local Tool Bridge ingress 8/8 PASS；real-process recovery/UDS hardening 3/3 PASS；CatDesk backend-auth test PASS、security tests 9/9 PASS。CatDesk 全量 217 PASS / 6 historical change_tracking baseline FAIL，未出现本轮新增失败。
- 下一步只对新的 immutable candidate 做独立复审；复审前不得把 SAFE-01 写成 CLOSED。

## LAB-SAFE-01 post-fix review round 1

- 独立 Grok 4.6 xhigh 对 immutable `2bbbdccdab53588f486fccad5126bf33636163e8` 完整返回 `FINDINGS`：0 Critical / 2 Important / 0 Minor。
- Important #1：CatDesk backend auth 在未设置 key env 时仍默认 Disabled。已在 CatDesk candidate `b94e85b0ba6b64e6a1dd360fa4838d80840794a2` 修复为默认 fail-closed；只有显式 `CATDESK_ALLOW_UNAUTHENTICATED_LOCAL_BACKEND=1` 才允许 legacy unauthenticated mode。`hardened_local_backend_mode_fails_closed_without_key_path` 与 direct loopback 401 测试 PASS。
- Important #2：macOS Seatbelt `(allow signal)` 可让 prompt-originated shell signal 外部 same-UID 进程。Bridge security core 与 CatDesk profile 已改为仅 `(target self)` + `(target children)`；新增真实外部 `/bin/sleep` signal-denied + child signal-allowed harness，PASS。
- Rotation cleanup 进一步收口：一旦 N+1 state 已提交，previous-key cleanup 失败不再把已完成 rotation 报成失败；新增 cleanup-failure recovery test。当前 security boundary 20/20 PASS。
- CatDesk security candidate 当前精确 HEAD=`b94e85b0ba6b64e6a1dd360fa4838d80840794a2`；backend default fail-closed 与 signal confinement 已提交。
- SAFE-01 仍保持 ACTIVE，等待新的 immutable main candidate 独立复审；不得提前写 PASS/CLOSED。

## LAB-SAFE-01 final closure

- Final independent Grok 4.6 xhigh Security Reviewer completed on immutable main `60e2fc855ea792d8a1e23a061c9328e340a96d1f` plus CatDesk external security candidate `b94e85b0ba6b64e6a1dd360fa4838d80840794a2`. Verdict: `PASS`; `critical=[]`, `important=[]`, `minor=[]`.
- Therefore LAB-SAFE-01 is formally `DONE / GO`. This GO is scoped to Local Tool Bridge Auth / Execution Session / Capability / Authorization / Workspace / Shell / backend-auth / recovery security; it does not upgrade Web host attestation and does not mean the whole product is complete.
- Current verified gates: security-boundary 20/20 PASS; local-tool-bridge ingress 8/8 PASS; real-process/UDS recovery 3/3 PASS; CatDesk targeted security 10/10 PASS; real Personal Plus Web scoped Files/Shell/Jobs E2E previously PASS.
- Next product critical path is LAB-COMP-01: independent Computer Runtime v0 API, capability/session ownership, bounded action semantics, Bridge E2E, and Runtime-OFF independence.


## 2026-09-08 Current continuation checkpoint

- main HEAD after current integration work: 916793e (Chrome Host attestation driver), preceded by 9065013 (Computer action fencing hardening).
- LAB-COMP-01 remains ACTIVE. Computer contract 8/8, native macOS 1/1, Bridge-to-Computer real E2E 1/1, Bridge ingress 10/10 and recovery 3/3 are PASS. Screen Recording is available and real bounded PNG screenshot is proven. Native Accessibility is currently false, so real find/click/type/key/scroll harmless fixture E2E remains blocked and must not be reported PASS.
- LAB-HOST-01 remains ACTIVE. main 916793e contains a Chrome MV3 debugger machine-boundary candidate, Native Messaging Host signer, 0600 Unix socket Runtime client, exact-origin installer and Runtime-facing verifier integration. Host + Chrome/native harness is 32/32 PASS, including a real native subprocess transport/sign/verify E2E. Real unpacked-extension machine attestation in Chrome is not yet completed; submit_continuation is explicitly not implemented.
- Runtime exact installed revision is 921ed8aaf09215ead06b215fbe81089799577616, impact live_assignments, manifest sha256 dcc5c589f4c6e9b02882ecdb5a009d244e048881ea8cad6d63b5e0686aa042af. The unique project Controller a697051d-402b-4062-a883-a29ba8b395e9 has ACKed this exact revision. live_e2e_required remains true and has not been accepted.
- Current Runtime canonical Web ownership is session 6a9e4037-0a00-83ea-bb8e-1301c5f6b979, generation 2, provenance manual_user_authorized, binding temporary, host_attested=false. This is not strong Host identity and must not be upgraded by prompt claims, URL/title/tab observation, manual continuation or manual_fenced receipts.
- LAB-GOV-02 and LAB-WEX-02 remain BLOCKED until product Host machine attestation and real same-target submit/re-entry complete the no-user-continue Live-E2E.
