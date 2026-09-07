# 任务台账

> Local Agent Bridge / Computer Runtime 项目的唯一执行控制面。只记录当前项目事实；实时容量、lease health、Reviewer 运行细节与 wake attempt 保留在 Runtime machine state，不在本台账复制第二套状态。

## 当前目标

- 当前 Goal：`LAB-SAFE-01` 在已通过的 Personal ChatGPT Web Plus → Custom Plugin → Secure MCP Tunnel → Local Bridge → Mac 链路上闭合 Auth / Execution Session / Capability / Authorization / Workspace / Shell 安全边界
- 下一可见检查点：`LAB-SAFE-01` 给出可测试的 request identity / execution session / capability / authorization contract，并用 hardlink、nonexistent-target write/move 与 shell breakout RED→GREEN harness 验证
- 当前活动项：`LAB-SAFE-01`（项目总控直接执行；无 Runtime assignment lease）
- 当前阻塞：产品关键路径无平台级阻塞；第一轮 PoC 已判定 `CONDITIONAL-GO`。Runtime 强身份 Live-E2E 仍是独立治理 debt，不阻塞 Local Bridge 安全化与产品开发
- 规则版本：Adaptive Agent Runtime `3032a7b57f898efc9872955c8886c8be11b48329`
- Web target reconciliation：`6a9e4158-cc8c-83ea-9763-05a7fd51f597` 已通过 canonical `bind-web-session` 加入唯一 Controller lineage；`identity=UNVERIFIED`, `binding_mode=historical_alias`, `reason=EXPLICIT_CURRENT_TARGET_REQUIRED`, `controller_actions_allowed=false`。旧 `6a9e4037-0a00-83ea-bb8e-1301c5f6b979` 仅为历史 alias；manual lease 尚未旋转，原因是 installed Runtime 要求先建立 verified explicit current Web target。


## 任务拆分

| ID | 状态 / 负责人 | 目标与边界 | 依赖 / 阻塞 | 验收与验证 | 证据 / 下一步 |
| --- | --- | --- | --- | --- | --- |
| LAB-GOV-01 | `DONE` / 项目总控 | 建立本项目最小 Adaptive Agent Runtime 治理：独立 project identity、唯一 logical Controller、temporary Web binding、协作文档骨架、规则安装兼容 | 不得复用 SelfAlone 身份；初始化期间发现 unborn Git 生命周期缺陷 | Runtime registry 唯一 Controller；manual lease 存在；unborn snapshot/restore 回归 PASS；修复安装到当前 Runtime | project identity `.git`；Controller `a697051d-402b-4062-a883-a29ba8b395e9`；Runtime `e639d0656d8161f02f00aace6f6d7daa374e349a` |
| LAB-GOV-02 | `BLOCKED` / 项目总控 | 最终闭合 Runtime continuation 强身份 Live-E2E：host-attested current target → confirmed wake → clean CLOSED controller cycle → accept-live-e2e | 当前只有 `manual_user_authorized / resume_only / host_attested=false`；用户已明确要求该缺口不得阻塞当前开发，Runtime `3032a7b...` 提供 revision-scoped deferral | 当前开发期要求 `current_deferred_live_e2e` + launch-guard allowed；最终仍必须取得真实 host-attested E2E，不得把 deferral 当 PASS | 继续 `LAB-ID-02`；新 Bridge identity/attestation 成立后恢复本任务并做真实验收 |
| LAB-BASE-01 | `DONE` / 项目总控 | 收编既有 CatDesk / chatgpt-local-coder PoC、旧 Web/桌面成果与安全调查，不重新克隆或重做已完成调查 | 以 Git/filesystem/CHECKPOINT 证据为准 | 既有路径、安全结论、未验证项和交接风险已恢复 | `CHECKPOINT.md`；CatDesk PoC `/Users/echoman/CatDesk-PoC-20260907`；下一步只在事实失效时复验 |
| LAB-ID-01 | `DONE` / 项目总控 | 建立最小只读 `identity_probe`，只观察和脱敏身份信号，不宣称认证 | 不开放完整 Files/Shell/Browser | 当前 `node --test experiments/identity-probe/server.test.mjs` 5/5 PASS；`oauthVerified=false` 明确保留；已兼容 ChatGPT `server/discover` / `tools/list` contract | `experiments/identity-probe/server.mjs`、`server.test.mjs` |
| LAB-ID-02 | `DONE / CONDITIONAL-GO` / 项目总控 | 第一轮核心 PoC：Personal ChatGPT Web Plus → Custom Plugin/MCP → OpenAI Secure MCP Tunnel → Local Bridge → Mac；真实完成 read/write/edit/command/job，同时捕获 identity/auth signals | 无平台级 blocker；当前 PoC harness 仍是固定能力、安全隔离版本，不代表通用 Files/Shell 已可上线 | 真实 Personal Web `tools/call` 已依次完成 `poc_read`、`poc_write`、`poc_edit`、`poc_command`、`poc_start_job`、`poc_poll_job`；本机文件最终为 `edited-by-chatgpt-web-poc`；job finished；正式判定 `CONDITIONAL-GO` | `Local Bridge Identity Probe` 已安装并连接；独立 Secure MCP Tunnel `tunnel_6a9e90a02f548191b3f2458617513158` 曾用于本轮证据；进入 `LAB-SAFE-01` |
| LAB-SAFE-01 | `ACTIVE` / 项目总控 | 闭合 Auth → Execution Session → Capability → Authorization → Workspace/Shell isolation；Bridge 只保留 model-agnostic transport/session/capability facts | `LAB-ID-02` 已完成；`x-openai-session`/`x-openai-subject` 目前只可作为 OpenAI tunnel correlation facts，尚不得等同 Web conversation / Runtime Controller identity | 先形成 contract + RED tests；hardlink escape、nonexistent-target write/move、shell breakout 必须 fail closed；再验证 fixed capability → scoped general capability 的升级 | 立即定义 threat model / request context / capability policy，并对已知 CatDesk 风险建 RED harness |
| LAB-COMP-01 | `BLOCKED` / 项目总控 | 定义并验证独立 Computer Runtime 最小 API、授权与 Bridge 调用 E2E | 依赖 `LAB-ID-02` 与安全能力边界；本轮不做 GUI/Computer 产品实现 | Bridge → Computer Runtime 的真实授权调用与隔离证据 | 等待 `LAB-ID-02`，本轮不实施 |

## 当前事实摘要

- 正式项目根：`/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge`
- Runtime project identity boundary：`/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge/.git`
- Git：`main`；已建立首个 governance baseline `b909f3ebcbabf92cd07245876949f7ad749ab4bb`，后续 worktree / delegated assignment 可基于稳定 revision。
- 唯一 logical Controller：`a697051d-402b-4062-a883-a29ba8b395e9`。
- 当前用户授权 Web execution target：`6a9e4158-cc8c-83ea-9763-05a7fd51f597`。
- Bootstrap binding：`provenance=manual_user_authorized`、`binding=temporary`、`host_attested=false`、Runtime lease mode=`resume_only`。
- 当前 `LAB-SAFE-01` 为 Controller 直接执行的 `ACTIVE` 产品工作；Runtime assignment state 仍无 non-terminal lease，因此不存在需要重复派发/恢复的 executor assignment。
- SelfAlone 是完全独立项目，只可作为未来外部 E2E target / fixture；不得修改其 main、复用其 Controller/台账或接管 lifecycle。

## 最近证据

| 任务 | 候选 / 环境 | 验证结果 | 证据位置 / 失效条件 |
| --- | --- | --- | --- |
| LAB-GOV-01 | installed Runtime | unborn project regression 已 PASS；installed Runtime 当前为 `3032a7b57f898efc9872955c8886c8be11b48329`，新增 Web host attestation unavailable 的显式 revision-scoped Live-E2E deferral | 若安装 revision 再变化需重新 handshake |
| LAB-GOV-01 | controller registry | 本项目只有 `a697051d-402b-4062-a883-a29ba8b395e9` 一个 Controller；Web lineage 含历史 `6a9e4037-0a00-83ea-bb8e-1301c5f6b979` 与当前用户授权 `6a9e4158-cc8c-83ea-9763-05a7fd51f597`；Runtime 尚无 explicit current Web target | `~/.codex/adaptive-delivery-controllers.json`；后续 canonical ownership/generation 变更时失效 |
| LAB-ID-01 | local Node test | 5 tests PASS / 0 FAIL | `node --test experiments/identity-probe/server.test.mjs`；实现或测试变化时重跑 |
| LAB-BASE-01 | historical PoC | hardlink read escape 已复现；CatDesk identity/shell/computer 边界仍不足以直接 Go | `CHECKPOINT.md` 与 `/Users/echoman/CatDesk-PoC-20260907`；若上游/实现变化需复验 |

| LAB-ID-02 | Local localhost multi-tools | `server/discover` + `tools/list` 成功；真实 read/write/edit/run_command/start+poll job 均在隔离 workspace 成功 | `experiments/web-local-poc/`；仅为调试证据，不能替代 Personal Web E2E |
| LAB-ID-02 | Dedicated identity HTTPS ingress | fresh localhost.run TLS tunnel → dedicated `identity_probe` on `127.0.0.1:33302`；公网 `server/discover` PASS；未暴露 CatDesk Files/Shell/Computer | 临时 URL/进程属于运行态，不写入长期架构或提交为凭据 |
| LAB-ID-02 | Plus Personal Plugins UI | 当前真实 Plus Personal 页面存在 `Plugins`、`Create app`；New Plugin 支持 `Server URL / Tunnel` 与 `OAuth / No Auth / Mixed` | 真实 ChatGPT Web UI；创建 app 与首次 tool call 后继续更新 |
| LAB-ID-02 | Safe Web-E2E harness local preflight | `safe-poc-server.mjs` 对固定隔离 workspace 的 read/write/edit/`/bin/pwd`/background job start+poll 全部本地 PASS；不接受任意路径或任意 shell | `experiments/web-local-poc/safe-poc-server.mjs`；仅为 N6 PREP，不能替代 Personal Web E2E |
| LAB-ID-02 | Personal Plus real Web E2E | `Local Bridge Identity Probe` 自定义 Plugin 经独立 OpenAI Secure MCP Tunnel 调用本机；真实 Web `tools/call` 依次命中 read/write/edit/fixed command/start job/poll job；workspace 最终写入 `edited-by-chatgpt-web-poc`，job 返回 finished | 脱敏证据：`experiments/web-local-poc/logs/identity-probe-web.jsonl`（运行态，不提交）；PoC 结论 `CONDITIONAL-GO` |
| LAB-ID-02 | Identity correlation | 同一 Web executor chat 的 `x-openai-session` 指纹在 6 次连续 tool calls 中稳定；`x-openai-subject` 指纹跨两个独立测试 chat 稳定；未观察到可直接等同 ChatGPT conversation ID 的机器字段 | 这些值当前只作为 OpenAI tunnel/session/principal correlation facts；未验证语义前不得用于 Runtime Controller host attestation |
| LAB-GOV-02 | Runtime rule handshake | Runtime `3032a7b57f898efc9872955c8886c8be11b48329` exact ACK；按用户明确“临时绑定不得阻塞开发”要求执行 revision-scoped Live-E2E deferral；`launch-guard allowed` | `current_deferred_live_e2e` 不是强身份 PASS；后续 Bridge host attestation 可用时必须恢复真实 Live-E2E |
