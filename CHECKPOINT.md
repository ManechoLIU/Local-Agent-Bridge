# Local Agent Bridge / Computer Runtime — Runtime Initialization Checkpoint

更新时间：2026-09-07（Asia/Singapore）

## 本轮范围

本轮仅完成 Adaptive Agent Runtime 最小开发治理初始化、已有 PoC 工作收编与初始化缺陷闭环；没有开始完整 Local Agent Bridge / Computer Runtime 产品开发，没有修改 SelfAlone main，也没有接管或复用 SelfAlone Controller。

## Canonical project facts

- 正式项目根：`/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge`
- Runtime project identity boundary：`/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge/.git`（Adaptive Agent Runtime 当前以 Git common-dir 作为项目身份边界；没有额外 project_id UUID 字段）
- Git branch：`main`
- Git HEAD：`null` / unborn；当前尚无首个 commit
- 唯一 logical Controller ID：`a697051d-402b-4062-a883-a29ba8b395e9`
- 当前用户授权 Web execution target：`6a9e4037-0a00-83ea-bb8e-1301c5f6b979`
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
