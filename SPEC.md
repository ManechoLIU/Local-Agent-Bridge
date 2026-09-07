# 产品规范

> 产品目标、非目标、行为、流程、状态和验收以此为准。当前只记录用户已明确确认的产品级约束；未确认细节不得擅自补全。

## 目标

- 建立新的 **Local Agent Bridge / Local Tool Bridge**，让 ChatGPT Web 或其他 Agent 能安全调用本机 Files、Shell、Process、Browser 与独立 Computer Runtime。
- Bridge 与 Computer Runtime 可以被 Adaptive Agent Runtime 调用，但产品必须支持不启动任何项目治理 Runtime 的独立运行模式。
- 建立机器可验证的 execution-session identity / host-session attestation / capability verification / execution-target identity，最终淘汰当前 manual Web Controller binding。
- 在安全边界成立后，提供足以支持真实开发工作的读写、进程、浏览器和桌面执行能力。

## 非目标

- 不把 Adaptive Agent Runtime、SelfAlone Controller、Goal 或 TASK_LEDGER 设计成 Bridge / Computer Runtime 的产品运行依赖。
- 不复用 SelfAlone 的 project identity、logical Controller 或 lifecycle。
- 不把 prompt 中的 conversation ID、随机 URL path、`Mcp-Session-Id`、进程存活或页面 DOM 观察当作 host-attested identity。
- 当前治理初始化阶段不实现完整 Files/Shell/Browser/Computer Runtime 产品能力。

## 用户流程

最终必须同时支持：

1. 独立模式：`ChatGPT Web / other agent -> Local Agent Bridge -> local tools / Computer Runtime -> local machine`。
2. 编排模式：`ChatGPT Web Controller -> Local Agent Bridge / executor adapters -> Adaptive Agent Runtime governance -> executors / reviewers`，其中 Runtime 只负责开发期 orchestration。

当前 Bootstrap 阶段允许用户显式授权单一 Web execution target 作为项目总控入口，但必须标记 `manual_user_authorized / temporary / host_attested=false`。

## 状态与失败行为

- 身份或 capability 无法机器验证时，Controller-sensitive / 高副作用动作必须按能力边界 fail closed；不得伪造 attestation。
- Bridge 局部能力失败不等于整个 Bridge 失效；按实际 capability/readiness 分层报告。
- 安全平台明确阻断某个动作时，不通过编码、混淆、拆碎或换执行器规避。
- Runtime orchestration 不可用时，Bridge / Computer Runtime 产品独立模式仍必须可运行。

## 验收标准

正式方案至少需要真实证明：

- `ChatGPT Web -> new Local Agent Bridge identity/attestation -> Adaptive Agent Runtime -> verified unique logical Controller`。
- 能够退役 `manual_user_authorized` Web Controller binding，不再要求用户用提示词提供 conversation ID 证明 Controller 身份。
- Files workspace 边界覆盖 traversal、symlink、hardlink、nonexistent-target 与 TOCTOU 风险。
- Capability/Authorization 必须绑定 authenticated principal、Bridge execution session、capability grant 与 workspace；任一不匹配时高副作用能力 fail closed。
- Bridge ingress 的授权证明必须是本地签名/等价机器证明并绑定具体请求内容、method/tool、nonce 与 session generation；OpenAI tunnel correlation headers 本身不是授权凭据。
- Shell/Process 有真实、可解释的 filesystem / network / environment / credential 权限边界，而非仅依赖 cwd。
- Browser 与独立 Computer Runtime 的授权、session ownership、目标隔离和 E2E 均有真实证据。
- 在 Adaptive Agent Runtime 完全未运行时，Bridge / Computer Runtime 核心产品链仍可独立工作。
