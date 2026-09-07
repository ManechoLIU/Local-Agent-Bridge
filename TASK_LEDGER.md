# 任务台账

> Local Agent Bridge / Computer Runtime 项目的唯一执行控制面。只记录当前项目事实；实时容量、lease health、Reviewer 运行细节与 wake attempt 保留在 Runtime machine state，不在本台账复制第二套状态。

## 当前目标

- 当前 Goal：`LAB-ID-02` 完成真实 ChatGPT Web → identity_probe 身份信号 E2E，确认可验证 identity/attestation 输入边界
- 下一可见检查点：`LAB-ID-02` 取得一次真实 Web 请求的脱敏 headers / MCP `_meta` / OAuth 验证事实，并给出 host/session attestation Go/No-Go 结论
- 当前活动项：无
- 当前阻塞：`LAB-GOV-02`：规则已 ACK，但 Runtime 为 `pending_live_e2e`；当前 temporary/manual Web binding 没有 host-attested explicit Web target，无法合法产生 `CONFIRMED` wake，因此新 Assignment launch 暂时 fail closed；高权限产品扩展继续受 `LAB-ID-02` 身份结论约束
- 规则版本：Adaptive Agent Runtime `e639d0656d8161f02f00aace6f6d7daa374e349a`

## 任务拆分

| ID | 状态 / 负责人 | 目标与边界 | 依赖 / 阻塞 | 验收与验证 | 证据 / 下一步 |
| --- | --- | --- | --- | --- | --- |
| LAB-GOV-01 | `DONE` / 项目总控 | 建立本项目最小 Adaptive Agent Runtime 治理：独立 project identity、唯一 logical Controller、temporary Web binding、协作文档骨架、规则安装兼容 | 不得复用 SelfAlone 身份；初始化期间发现 unborn Git 生命周期缺陷 | Runtime registry 唯一 Controller；manual lease 存在；unborn snapshot/restore 回归 PASS；修复安装到当前 Runtime | project identity `.git`；Controller `a697051d-402b-4062-a883-a29ba8b395e9`；Runtime `e639d0656d8161f02f00aace6f6d7daa374e349a` |
| LAB-GOV-02 | `BLOCKED` / 项目总控 | 闭合 installed Runtime `e639d0656d8161f02f00aace6f6d7daa374e349a` 的真实 continuation Live-E2E：confirmed wake → clean CLOSED controller cycle → accept-live-e2e | 当前只有 `manual_user_authorized / resume_only / host_attested=false` Web alias；无 canonical manual current-Web-target 升格入口；`native-stop --dry-run` 仍解析到 desktop logical Controller ID | `rule_handshake status` 必须转为 `current` 且 launch-guard allowed；不得伪造 wake/target/receipt | 等待 `LAB-ID-02` 或新的 Bridge host/session attestation 能力提供机器可信 Web target，再走 canonical same-controller recovery |
| LAB-BASE-01 | `DONE` / 项目总控 | 收编既有 CatDesk / chatgpt-local-coder PoC、旧 Web/桌面成果与安全调查，不重新克隆或重做已完成调查 | 以 Git/filesystem/CHECKPOINT 证据为准 | 既有路径、安全结论、未验证项和交接风险已恢复 | `CHECKPOINT.md`；CatDesk PoC `/Users/echoman/CatDesk-PoC-20260907`；下一步只在事实失效时复验 |
| LAB-ID-01 | `DONE` / 项目总控 | 建立最小只读 `identity_probe`，只观察和脱敏身份信号，不宣称认证 | 不开放完整 Files/Shell/Browser | `node --test experiments/identity-probe/server.test.mjs` 4/4 PASS；`oauthVerified=false` 明确保留 | `experiments/identity-probe/server.mjs`、`server.test.mjs`；进入真实 Web E2E |
| LAB-ID-02 | `READY` / 项目总控 | 从真实 ChatGPT Web 入口调用 identity_probe，确认实际 headers、MCP `_meta`、OAuth/client、conversation/session correlation；只做身份链路验证 | 需要真实 Web → Bridge/MCP 请求链；不得把 prompt ID、URL secret、Mcp-Session-Id 单独视为 host attestation | 形成脱敏观测证据；明确哪些信号可机器验证、哪些只是相关性；给出 attestation/challenge/capability 下一步 | 下一步：建立可从当前 Web 真实调用的最小 probe 暴露链并执行一次真实请求 |
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
| LAB-GOV-01 | installed Runtime | unborn project regression 定向 8/8 PASS；installed manifest 已更新为 `e639d0656d8161f02f00aace6f6d7daa374e349a` | Runtime fix branch `fix/unborn-project-init`；若安装 revision 改变需重新 handshake |
| LAB-GOV-01 | controller registry | 本项目只有 `a697051d-402b-4062-a883-a29ba8b395e9` 一个 Controller；Web alias 为 `6a9e4037-0a00-83ea-bb8e-1301c5f6b979` | `~/.codex/adaptive-delivery-controllers.json`；后续 canonical ownership/generation 变更时失效 |
| LAB-ID-01 | local Node test | 4 tests PASS / 0 FAIL | `node --test experiments/identity-probe/server.test.mjs`；实现或测试变化时重跑 |
| LAB-BASE-01 | historical PoC | hardlink read escape 已复现；CatDesk identity/shell/computer 边界仍不足以直接 Go | `CHECKPOINT.md` 与 `/Users/echoman/CatDesk-PoC-20260907`；若上游/实现变化需复验 |
