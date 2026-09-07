# 任务台账

> Local Agent Bridge / Computer Runtime 项目的唯一执行控制面。只记录当前项目事实；实时容量、lease health、Reviewer 运行细节与 wake attempt 保留在 Runtime machine state，不在本台账复制第二套状态。

## 当前目标

- 当前 Goal：`LAB-ID-02` 完成第一轮 Personal ChatGPT Web Plus → Custom MCP → 隔离 CatDesk/New Bridge → Mac 真实 E2E，并在同一链路捕获身份信号
- 下一可见检查点：`LAB-ID-02` 从 Personal ChatGPT Plus 实际完成 read → write → edit → harmless command → long-running job，并形成脱敏 identity/auth 观测与 GO / CONDITIONAL-GO / NO-GO 结论
- 当前活动项：无
- 当前阻塞：用户需在 AI-Bridge 本地确认卡允许打开 `chatgpt.com` Plugins/Connector 页面；Runtime 强身份 Live-E2E 已按用户明确要求走 revision-scoped deferral，不再阻塞本项目开发
- 规则版本：Adaptive Agent Runtime `3032a7b57f898efc9872955c8886c8be11b48329`

## 任务拆分

| ID | 状态 / 负责人 | 目标与边界 | 依赖 / 阻塞 | 验收与验证 | 证据 / 下一步 |
| --- | --- | --- | --- | --- | --- |
| LAB-GOV-01 | `DONE` / 项目总控 | 建立本项目最小 Adaptive Agent Runtime 治理：独立 project identity、唯一 logical Controller、temporary Web binding、协作文档骨架、规则安装兼容 | 不得复用 SelfAlone 身份；初始化期间发现 unborn Git 生命周期缺陷 | Runtime registry 唯一 Controller；manual lease 存在；unborn snapshot/restore 回归 PASS；修复安装到当前 Runtime | project identity `.git`；Controller `a697051d-402b-4062-a883-a29ba8b395e9`；Runtime `e639d0656d8161f02f00aace6f6d7daa374e349a` |
| LAB-GOV-02 | `BLOCKED` / 项目总控 | 最终闭合 Runtime continuation 强身份 Live-E2E：host-attested current target → confirmed wake → clean CLOSED controller cycle → accept-live-e2e | 当前只有 `manual_user_authorized / resume_only / host_attested=false`；用户已明确要求该缺口不得阻塞当前开发，Runtime `3032a7b...` 提供 revision-scoped deferral | 当前开发期要求 `current_deferred_live_e2e` + launch-guard allowed；最终仍必须取得真实 host-attested E2E，不得把 deferral 当 PASS | 继续 `LAB-ID-02`；新 Bridge identity/attestation 成立后恢复本任务并做真实验收 |
| LAB-BASE-01 | `DONE` / 项目总控 | 收编既有 CatDesk / chatgpt-local-coder PoC、旧 Web/桌面成果与安全调查，不重新克隆或重做已完成调查 | 以 Git/filesystem/CHECKPOINT 证据为准 | 既有路径、安全结论、未验证项和交接风险已恢复 | `CHECKPOINT.md`；CatDesk PoC `/Users/echoman/CatDesk-PoC-20260907`；下一步只在事实失效时复验 |
| LAB-ID-01 | `DONE` / 项目总控 | 建立最小只读 `identity_probe`，只观察和脱敏身份信号，不宣称认证 | 不开放完整 Files/Shell/Browser | `node --test experiments/identity-probe/server.test.mjs` 4/4 PASS；`oauthVerified=false` 明确保留 | `experiments/identity-probe/server.mjs`、`server.test.mjs`；进入真实 Web E2E |
| LAB-ID-02 | `READY` / 项目总控 | 第一轮核心 PoC：Personal ChatGPT Web Plus → Custom MCP → 隔离 CatDesk/proxy → Mac；真实完成 read/write/edit/command/job，同时捕获 Connector/MCP identity/auth signals | 当前宿主没有可靠 Codex/sub-agent spawn；总控按项目规则直接用 Local Bridge 推进；本机 multi-tools 已通过；最终证据必须来自 Personal ChatGPT Web，不得用 curl/local client 代替 | 五项 Web→Local 操作真实成功；脱敏记录 headers/_meta/auth；区分可信协议事实与客户端自声明；立即给出 GO/CONDITIONAL-GO/NO-GO | 当前需完成 Plus Personal Connector UI 接入并执行一次真实 Web 调用 |
| LAB-SAFE-01 | `BLOCKED` / 项目总控 | 在身份边界明确后继续 workspace write/move/hardlink、Shell sandbox 与 capability 风险闭环 | 依赖 `LAB-ID-02`；身份与授权边界未确定前不扩大本机副作用面 | 只在身份结论后定义并执行相称安全 Harness | 等待 `LAB-ID-02` 身份结论 |
| LAB-COMP-01 | `BLOCKED` / 项目总控 | 定义并验证独立 Computer Runtime 最小 API、授权与 Bridge 调用 E2E | 依赖 `LAB-ID-02` 与安全能力边界；本轮不做 GUI/Computer 产品实现 | Bridge → Computer Runtime 的真实授权调用与隔离证据 | 等待 `LAB-ID-02`，本轮不实施 |

## 当前事实摘要

- 正式项目根：`/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge`
- Runtime project identity boundary：`/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge/.git`
- Git：`main`；已建立首个 governance baseline `b909f3ebcbabf92cd07245876949f7ad749ab4bb`，后续 worktree / delegated assignment 可基于稳定 revision。
- 唯一 logical Controller：`a697051d-402b-4062-a883-a29ba8b395e9`。
- 当前 Web execution target：`6a9e4037-0a00-83ea-bb8e-1301c5f6b979`。
- Bootstrap binding：`provenance=manual_user_authorized`、`binding=temporary`、`host_attested=false`、Runtime lease mode=`resume_only`。
- 当前没有 `ACTIVE` / `RECOVERING` 台账项，也没有已发现的 non-terminal assignment lease；不得重复派发不存在的旧 ACTIVE 工作。
- SelfAlone 是完全独立项目，只可作为未来外部 E2E target / fixture；不得修改其 main、复用其 Controller/台账或接管 lifecycle。

## 最近证据

| 任务 | 候选 / 环境 | 验证结果 | 证据位置 / 失效条件 |
| --- | --- | --- | --- |
| LAB-GOV-01 | installed Runtime | unborn project regression 已 PASS；installed Runtime 当前为 `3032a7b57f898efc9872955c8886c8be11b48329`，新增 Web host attestation unavailable 的显式 revision-scoped Live-E2E deferral | 若安装 revision 再变化需重新 handshake |
| LAB-GOV-01 | controller registry | 本项目只有 `a697051d-402b-4062-a883-a29ba8b395e9` 一个 Controller；Web alias 为 `6a9e4037-0a00-83ea-bb8e-1301c5f6b979` | `~/.codex/adaptive-delivery-controllers.json`；后续 canonical ownership/generation 变更时失效 |
| LAB-ID-01 | local Node test | 4 tests PASS / 0 FAIL | `node --test experiments/identity-probe/server.test.mjs`；实现或测试变化时重跑 |
| LAB-BASE-01 | historical PoC | hardlink read escape 已复现；CatDesk identity/shell/computer 边界仍不足以直接 Go | `CHECKPOINT.md` 与 `/Users/echoman/CatDesk-PoC-20260907`；若上游/实现变化需复验 |

| LAB-ID-02 | Local localhost multi-tools | `server/discover` + `tools/list` 成功；真实 read/write/edit/run_command/start+poll job 均在隔离 workspace 成功 | `experiments/web-local-poc/`；仅为调试证据，不能替代 Personal Web E2E |
| LAB-ID-02 | Temporary HTTPS ingress | fresh localhost.run tunnel 的公网 `server/discover` 已经通过 TLS → identity proxy → local CatDesk，证明 transport ingress 可达 | 临时 tunnel 只暴露隔离 PoC workspace；URL/进程属于运行态，不写入长期架构或提交为凭据 |
| LAB-GOV-02 | Runtime rule handshake | Runtime `3032a7b57f898efc9872955c8886c8be11b48329` exact ACK；按用户明确“临时绑定不得阻塞开发”要求执行 revision-scoped Live-E2E deferral；`launch-guard allowed` | `current_deferred_live_e2e` 不是强身份 PASS；后续 Bridge host attestation 可用时必须恢复真实 Live-E2E |
