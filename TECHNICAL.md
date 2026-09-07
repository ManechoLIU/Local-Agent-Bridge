# 技术规范

> 架构、数据、接口、安全、Harness、发布和恢复以此为准。当前仅冻结已确认边界，不提前锁死实现语言或未验证协议细节。

## 架构

长期架构必须保持解耦：

`Agent / ChatGPT Web -> Local Agent Bridge -> Files / Shell / Process / Browser / Computer Runtime -> local machine`

Adaptive Agent Runtime 可以作为开发期 orchestration infrastructure，通过 adapter 调用 Bridge；Bridge 也可以暴露 Runtime 所需的 assignment/lifecycle 适配信息。但核心 Bridge / Computer Runtime 不得要求 Runtime、Controller registry、Goal 或 TASK_LEDGER 才能启动和执行本机能力。

## 数据与接口

身份层必须显式区分：

- Web conversation / execution target identity；
- Bridge execution session identity；
- logical Controller identity；
- Runtime registration / ownership / generation。

未来强绑定接口需要至少覆盖 execution-session authentication、host/session attestation、challenge/capability verification、execution-target identity、过期/撤销与 replay 防护。当前 `manual_user_authorized` 仅是 Bootstrap 治理授权，不是协议设计。

## 安全与外部依赖

- 不以 URL secret 或 transport session ID 代替 authenticated principal / capability。
- Files 隔离必须覆盖 hardlink、symlink、path canonicalization、nonexistent target 与 TOCTOU。
- Shell/Process 必须定义 OS 级或等价可验证的权限边界；cwd 不是 sandbox。
- Browser DevTools bridge 与通用 Computer Runtime 是不同 capability，分别授权、审计和隔离。
- 日志不得持久化 bearer token、cookie、API key 或可直接复用的敏感凭据；身份探针只保留脱敏指纹/相关性事实。

## Harness

当前最小 Harness：

- `experiments/identity-probe/server.test.mjs`：身份探针协议、脱敏与 fail-closed 测试。
- Runtime governance：ledger consistency、strict governance lint、rule handshake、lifecycle snapshot。

后续按风险增加：真实 Web identity E2E、workspace escape Harness、Shell sandbox Harness、Browser/Computer Runtime ownership E2E。

## 发布与恢复

- 身份链未形成机器可信证据前，不把 PoC 描述为安全 Bridge Go。
- 高副作用能力必须在对应身份/授权/隔离 Harness 通过后逐步开放。
- Runtime 开发期故障不得通过伪造 machine receipt 解锁；产品自身恢复也必须区分 process liveness、session continuity 与授权 continuity。
