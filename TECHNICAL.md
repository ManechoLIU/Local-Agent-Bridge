# 技术规范

> 架构、数据、接口、安全、Harness、发布和恢复以此为准。当前仅冻结已确认边界，不提前锁死实现语言或未验证协议细节。

## 架构

长期架构必须保持两条可组合但独立的链：

产品本机工具链：Personal ChatGPT Web / other agent -> Local Agent Bridge -> Files / Shell / Process / Browser / Computer Runtime -> local machine。

Web Executor 链：Adaptive Agent Runtime -> canonical route -> Web ChatGPT Executor Adapter -> authenticated Personal ChatGPT Web session -> ChatGPT conversation -> Web model -> result/terminal -> SAME Controller continuation。

需要本机能力时，只在 Executor 侧组合：Web ChatGPT Executor -> Local Tool Bridge -> local capabilities。

Adaptive Agent Runtime 可以作为开发期 orchestration infrastructure；核心 Bridge / Computer Runtime 不得要求 Runtime、Controller registry、Goal、TASK_LEDGER、Web model routing 或 Web conversation lifecycle 才能启动和执行本机能力。

## 数据与接口

身份层必须显式区分：

- Web conversation / execution target identity；
- Bridge execution session identity；
- logical Controller identity；
- Runtime registration / ownership / generation。

未来强绑定接口需要至少覆盖 execution-session authentication、host/session attestation、challenge/capability verification、execution-target identity、过期/撤销与 replay 防护。当前 `manual_user_authorized` 仅是 Bootstrap 治理授权，不是协议设计。


Web Executor machine state 只保存 execution correlation，不成为第二份 TASK_LEDGER。最小字段包括 assignment_id、execution_id、attempt、browser_session_id、conversation_id（若可观察）、provider/model requested/observed、model_attestation、status、started/updated/terminal timestamps、result locator、terminal receipt、recovery generation 与 provenance。Runtime canonical Assignment lifecycle 仍是最终状态权威。

## 安全与外部依赖

- 不以 URL secret 或 transport session ID 代替 authenticated principal / capability。
- Files 隔离必须覆盖 hardlink、symlink、path canonicalization、nonexistent target 与 TOCTOU。
- Shell/Process 必须定义 OS 级或等价可验证的权限边界；cwd 不是 sandbox。
- Browser DevTools bridge 与通用 Computer Runtime 是不同 capability，分别授权、审计和隔离。
- 日志不得持久化 bearer token、cookie、API key 或可直接复用的敏感凭据；身份探针只保留脱敏指纹/相关性事实。

## LAB-SAFE-01 security contract

Bridge authorization is explicitly layered as `Transport/Auth -> Execution Session -> Capability Grant -> Authorization -> Workspace/Shell enforcement`. The Bridge may report provider/tunnel correlation facts, but Web conversation identity, Runtime Controller identity and model routing remain outside this security core.

The first concrete contract is:

- `principal_id` identifies the authenticated/local transport principal used by Bridge authorization;
- `execution_session_id` identifies one Bridge execution session and is independently revocable;
- `capability_id` selects one issued capability grant;
- `workspace_id` selects the authorized filesystem/shell boundary;
- authorization succeeds only when all four values match and the requested operation is present in the grant.

Unix Files enforcement must resolve from an already-open workspace directory descriptor instead of relying only on `canonicalize()+starts_with()`: parent components use `openat` with `O_DIRECTORY|O_NOFOLLOW`; read/write targets use `O_NOFOLLOW` plus inode/link-count checks; create uses `O_CREAT|O_EXCL`; move uses `fstatat(..., AT_SYMLINK_NOFOLLOW)` and `renameat`; unsafe cross-device fallback fails closed. This closes the already-reproduced hardlink and nonexistent-target-through-symlink-parent classes in the current candidate.

macOS Shell enforcement currently uses a replaceable Seatbelt/sandbox-exec PoC backend. It clears inherited environment variables, moves runtime HOME/TMP into the workspace, blocks user-data roots outside the workspace, blocks outbound network access and permits workspace I/O. This is evidence that `cwd` can be replaced with a real OS-enforced boundary; `sandbox-exec` itself is not frozen as the final backend. Linux Landlock remains a separate backend; Windows requires an equivalent product decision.

Bridge ingress 的下一层已验证为 **local-ingress signed request proof**：本地 ingress adapter 持有 per-capability signing secret，逐请求签名 `capability_id + principal_id + execution_session_id + workspace_id + request_id + nonce + MCP method + tool name + body sha256 + provenance`。Bridge core 只接受 HMAC 校验成功且未过期/未撤销/未 replay、session generation 当前、capability scope 匹配的请求。签名 secret 不进入 ChatGPT prompt、MCP tool arguments、OpenAI tunnel headers 或普通日志。

`x-openai-session` / `x-openai-subject` 继续只记为 correlation observations；即使字段稳定，也不能单独生成上述 proof，因此不会被提升为 authorization source。Capability authority 的 PoC 状态使用 0600 key/state 文件原子持久化，replay nonce 与 revocation 在进程重启后继续生效。正式产品仍需把 signer/verifier 接入真实 loopback/Unix-socket ingress，并定义 key rotation、crash recovery 与同 UID hostile-process 边界。

正式 Bridge 进程边界开始收敛为两个 crate：`bridge-security-core` 负责 capability authority 与 workspace/shell enforcement；`local-tool-bridge` 负责 model-agnostic MCP ingress、local authentication、method/tool→capability 映射和 signed request proof，再调用 loopback/Unix-socket backend。该分层不包含 Web model routing、Controller lifecycle 或 DAG。

Operational authority state uses a 0700 private directory, versioned 0600 master-key files and a 0600 state snapshot. Master-key rotation is generation-fenced and crash ordered: write/sync the new key generation first, then atomically publish state that points to it. Request replay state is persisted before forwarding to any side-effecting backend; persistence failure returns an error and no backend call occurs.

### Local ingress transport

正式候选优先使用 Unix-domain socket 而不是额外 loopback TCP shared-secret hop：socket 以 0600 创建，`tunnel-client`/本机 adapter 连接 UDS 后，Local Tool Bridge 才生成 per-request HMAC capability proof 并转发到 loopback-only backend。这样 OpenAI tunnel 只到本机 transport adapter，ChatGPT/Web 不需要持有 capability signing secret。UDS 仍不是同 UID 恶意进程的强隔离边界；若威胁模型包含 hostile same-UID process，需要额外 worker ownership/peer-credential/code-signing 或独立用户/沙箱边界。

### Verified Secure MCP production-shaped path

当前已验证的产品形状为 `Personal ChatGPT Web -> OpenAI Secure MCP Tunnel -> tunnel-client direct unix_socket -> 0600 Local Tool Bridge UDS -> signed capability proof -> loopback-only hardened CatDesk backend -> Files/Shell/Jobs -> Mac`。tunnel-client 原生支持 HTTP-over-Unix-socket (`url=... + unix_socket=...`)；因此不需要为了 Secure MCP 再暴露一个 Local Tool Bridge TCP listener。Web/Cloud 一侧只知道 tunnel，不持有 Local Tool Bridge capability signing secret。

此链已经在真实 Personal Plus Chat 中完成 read/write/edit/harmless command/background job。安全层仍把 OpenAI tunnel correlation 与本地 authorization 分开；当前 operational key lifecycle、crash/replay recovery 与 v1 same-UID scope 已明确；上线前剩余安全门是修复后 immutable candidate 的独立复审与 packaging。

### v1 same-UID threat decision

当前 v1 安全目标明确覆盖 remote/cloud/prompt-originated adversary、其他 OS 用户、capability replay/expiry/revocation、workspace path/inode escape 和 Shell filesystem/network/environment escape；不宣称隔离已经以同一 macOS UID 运行的恶意本地进程。0600 UDS 与 0700/0600 state files 用于隔离其他用户和减少偶然访问，不是 same-UID malware boundary。当前 CatDesk loopback backend 因此只作为迁移期 backend；更高保证版本应把 Files/Shell/Jobs 实现直接收进 Local Tool Bridge 或放到独立 OS principal/sandboxed worker 后面。

这项 scope decision 不影响 `x-openai-*` 身份诚实性：OpenAI correlation headers 仍不成为 authorization source。

### Backend authentication and UDS peer boundary

Local Tool Bridge 的 UDS transport boundary 现在要求 socket parent 为 current-euid-owned real directory 并收紧至 0700，socket 本身为 0600；macOS 使用 `getpeereid`、Linux 使用 `SO_PEERCRED` 校验连接 peer UID。Bridge backend URL 只接受 numeric loopback IP，禁止 hostname、credentials、query/fragment，并关闭 HTTP redirect。Bridge 与迁移期 CatDesk backend 之间使用独立 0600 本机 key；CatDesk 在配置该 key 时对无 key/错误 key 的直接 loopback 请求返回 401。该 backend key 不属于 Web/OpenAI identity，也不替代 capability grant。

Authority state fail-closed：如果 state snapshot 缺失但 versioned key material 已存在，不会新建空 authority；restart 使用相同 capability ID 时，persisted grant 必须与当前 principal/session/workspace/capability set 完全一致，否则要求显式换 capability identity。capability set 参与派生 signing secret，防止本地 state scope 被静默改写。

## Harness

当前最小 Harness：

- `experiments/identity-probe/server.test.mjs`：身份探针协议、脱敏与 fail-closed 测试。
- Runtime governance：ledger consistency、strict governance lint、rule handshake、lifecycle snapshot。

后续按风险增加：真实 Web identity E2E、workspace escape Harness、Shell sandbox Harness、Browser/Computer Runtime ownership E2E。

## 发布与恢复

- 身份链未形成机器可信证据前，不把 PoC 描述为安全 Bridge Go。
- 高副作用能力必须在对应身份/授权/隔离 Harness 通过后逐步开放。
- Runtime 开发期故障不得通过伪造 machine receipt 解锁；产品自身恢复也必须区分 process liveness、session continuity 与授权 continuity。
