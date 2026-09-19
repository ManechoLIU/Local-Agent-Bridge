# 技术规范

> 架构、数据、接口、安全、Harness、发布和恢复以此为准。当前仅冻结已确认边界，不提前锁死实现语言或未验证协议细节。

## 架构

长期架构必须保持两条可组合但独立的链：

产品本机工具链：Personal ChatGPT Web / other agent -> Local Agent Bridge -> Files / Shell / Process / Browser / Computer Runtime -> local machine。

Web Executor 链：Adaptive Agent Runtime -> canonical route -> Web ChatGPT Executor Adapter -> authenticated Personal ChatGPT Web session -> ChatGPT conversation -> Web model -> result/terminal -> SAME Controller continuation。

需要本机能力时，只在 Executor 侧组合：Web ChatGPT Executor -> Local Tool Bridge -> local capabilities。

Adaptive Agent Runtime 可以作为开发期 orchestration infrastructure；核心 Bridge / Computer Runtime 不得要求 Runtime、Controller registry、Goal、TASK_LEDGER、Web model routing 或 Web conversation lifecycle 才能启动和执行本机能力。

Web Host Attestation 链：Adaptive Agent Runtime -> trusted verifier interface -> Web Host Attestation + Re-entry Adapter -> browser/Host machine boundary -> exact ChatGPT Web target。该 adapter 不属于 Local Tool Bridge，也不拥有 Runtime lifecycle；它只提供 Host identity attestation、freshness/replay verification 与 exact-target re-entry/submit receipt。

## 数据与接口

身份层必须显式区分：

- Web conversation / execution target identity；
- Bridge execution session identity；
- logical Controller identity；
- Runtime registration / ownership / generation。

未来强绑定接口需要至少覆盖 execution-session authentication、host/session attestation、challenge/capability verification、execution-target identity、过期/撤销与 replay 防护。当前 `manual_user_authorized` 仅是 Bootstrap 治理授权，不是协议设计。


Web Executor machine state 只保存 execution correlation，不成为第二份 TASK_LEDGER。最小字段包括 assignment_id、execution_id、attempt、browser_session_id、conversation_id（若可观察）、provider/model requested/observed、model_attestation、status、started/updated/terminal timestamps、result locator、terminal receipt、recovery generation 与 provenance。Runtime canonical Assignment lifecycle 仍是最终状态权威。

### Computer Runtime v1 bounded desktop actions

Computer Runtime 当前候选把桌面能力放在独立 `crates/computer-runtime`，不依赖 Adaptive Agent Runtime 或 AI-Bridge Compute Helper。MCP 动作面包括 `computer_list_apps/list_windows/active_window`、`computer_activate_app`、`computer_screenshot`、`computer_find`、`computer_click`、`computer_type`、`computer_key`、`computer_scroll`。每个 session-bound 调用必须携带 `expected_session_generation`；可作用于窗口的动作还必须引用 fresh observation 中的 exact `(window_id,pid)`，click 必须引用由同 session 的 find observation 产生的 opaque element locator。observation 有 TTL 和 bounded history，stale/missing generation、forged window/element、ownership mismatch 均在 provider side effect 前 fail closed。

macOS native helper v1 直接使用 CGWindow/Accessibility/CoreGraphics；screenshot 要求 Screen Recording，并限制尺寸/编码字节；find/click/type/key/scroll 要求 Accessibility，且 mutation 前重新核验 observed window 仍是目标、需要输入的动作要求 exact focused Accessibility window。find locator 绑定 AX path + element fingerprint，click 时重新 resolve 并验证 fingerprint，降低 stale element 误击风险。当前实机 Screen Recording=true、Accessibility=false，所以 native screenshot 已真实 E2E，UI mutation 仍必须等待授权后在 harmless fixture 上验证，不可用 mock 结果冒充 real action PASS。

## Web Host Attestation + Re-entry contract

该 capability 是独立 adapter，不是 Local Tool Bridge / Computer Runtime 子模块。Runtime OFF 时 Bridge / Computer Runtime 仍可独立运行；只有需要 Web Controller strong identity / unattended continuation 时 Runtime 才调用该 adapter。

### Machine target observation

Host adapter 必须从 browser/Host machine integration 读取当前 top-level document target，而不是从 prompt、DOM 文本、页面标题、截图或模型自报身份。最小 machine observation 绑定：`host_type=chatgpt_web`、browser target/tab identity、top-frame identity、document/loader identity、canonical secure origin、exact conversation identity、observation timestamp 与 machine-source provenance。conversation route 即使来自 browser navigation state，也只有与 target/frame/document identity 一起由 Host 机器边界签名后才可验证；单纯 URL 不具有 strong identity 语义。

当前 browser integration 已实机证明可在不读 DOM 的情况下取得 top-frame `frame id + loader id + securityOrigin` 等 browser machine facts；这仅证明可行 input surface，不等于 Runtime attestation PASS。不得把 `x-openai-session / x-openai-subject` 或 `ui_observed conversation_id` 升级为 strong identity。

### Attestation envelope

v1 envelope 至少绑定：`conversation_id`、`host_type`、`browser_target_id`、`top_frame_id`、`document_id/loader_id`、`issued_at/expires_at`、Runtime challenge nonce、Host nonce、`target_generation`、`ownership_generation`、Host provenance、receipt id 与 Host authentication tag/signature。Runtime verifier 必须验证 freshness、challenge exact match、generation/ownership、target/conversation consistency，并对 challenge/receipt 做 consume-once anti-replay。

Verifier 返回的是 verified target claims，不修改 Controller registry、Assignment lifecycle 或 TASK_LEDGER。`manual_user_authorized / temporary` 不得由 verifier 自动升级；只有真实 machine attestation 才可标 `host_attested=true`。

### Re-entry / wake

Re-entry request 绑定 verified target、fresh wake id/challenge、continuation payload hash、expected target generation 与 ownership generation。adapter 在 focus/submit **前**重新观察 machine target并二次校验；focus 后、submit 前再次 fence；submit 后返回 Host receipt，明确区分 `dispatch_attempted` 与 `submit_confirmed`。old target / stale generation / wrong conversation / target replacement / transient unavailable 均 fail closed 或返回可重试的 machine status，不得伪造成功。

Runtime 继续复用既有 SAME-Controller continuation、generation fencing、ownership fencing、bounded rearm 与 canonical lifecycle；Host adapter 只补齐 identity verifier + exact-target re-entry 最后一公里，不另造第二 wake system。

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

### Hardened backend mode and signal confinement

迁移期 CatDesk backend 默认进入 authenticated local-backend mode：未提供有效 `CATDESK_LOCAL_BACKEND_KEY_FILE` 时 hardened backend 不启动可用 MCP；只有显式 `CATDESK_ALLOW_UNAUTHENTICATED_LOCAL_BACKEND=1` 才进入 legacy compatibility mode。这样 Bridge 的 backend authentication 不依赖部署者“记得设置 env”。

macOS shell Seatbelt 的 signal 权限只允许 `target self` 与 `target children`；prompt-originated shell 不得 signal Bridge、CatDesk 或其他外部 same-UID 进程。Harness 同时验证 outside process signal 失败、child signal 成功。

## Harness

当前最小 Harness：

- `experiments/identity-probe/server.test.mjs`：身份探针协议、脱敏与 fail-closed 测试。
- Runtime governance：ledger consistency、strict governance lint、rule handshake、lifecycle snapshot。

后续按风险增加：真实 Web identity E2E、workspace escape Harness、Shell sandbox Harness、Browser/Computer Runtime ownership E2E。

## 发布与恢复

- 身份链未形成机器可信证据前，不把 PoC 描述为安全 Bridge Go。
- 高副作用能力必须在对应身份/授权/隔离 Harness 通过后逐步开放。
- Runtime 开发期故障不得通过伪造 machine receipt 解锁；产品自身恢复也必须区分 process liveness、session continuity 与授权 continuity。


## Chrome Host machine-boundary candidate (2026-09-08)

main 916793e adds an independent product-side Chrome Host path for LAB-HOST-01. The MV3 extension uses chrome.debugger target discovery plus Target.getTargetInfo and Page.getFrameTree. A ChatGPT route is only candidate-location input; after debugger attach the exact target id and top-frame conversation route must agree, with top-frame id, loader id, https://chatgpt.com origin and fresh timestamp bound into the machine observation. Duplicate targets for the same conversation fail closed. DOM text, title, screenshot content, prompt claims and x-openai correlation headers are not accepted as identity evidence.

The Host HMAC key remains in a local Native Messaging process. Runtime-facing calls use a 0600 Unix socket with request-id fencing; the state directory is 0700 and installer-generated Host key is 0600. The installer writes an exact-extension-origin Native Messaging manifest. This remains a same-user boundary and does not claim hostile same-UID isolation.

The Runtime-facing integration exposes challenge issuance, Host attestation request, verifier consumption and re-entry delegation, but owns no Runtime lifecycle, Assignment ledger, retry scheduler or wake system. The Host harness is now 56/56 PASS. Real Chrome for Testing and the existing authenticated Personal Web profile both load the fixed-ID extension and connect to the Native Messaging Host; authenticated exact-conversation Host-signed attestation and a real confirmed submit have passed. `runtime-verifier-cli.mjs` now performs fresh challenge → Host machine evidence → exact target/generation/ownership verification and persists a one-shot verified-target receipt that survives verifier restart; `submit_reentry` can consume only that receipt, never a caller-supplied verified object. The Host installer can optionally register the verifier for Adaptive Agent Runtime using a 0600 config and hashes all five transitive verifier modules. Installed Runtime `d1de19b773b09a247d41c225071a0d48d359c117` now implements the matching trusted external-verifier + submit-adapter protocol, revalidates the executable and full verifier bundle before each execution, and has been canonical-ACKed by the unique project Controller. Installation/ACK is still not final strong identity evidence: LAB-GOV-02 and LAB-WEX-02 remain blocked until canonical ownership reconciliation and final lifecycle Live-E2E complete.

## 2026-09-17：迁移边界与混合 Computer Engine 目标合同

该节记录用户已确认的后续方向，不升级现有 v1 的完成状态。

### 迁移与身份分层

`LAB Connect -> model-agnostic Tool Plane -> local backends` 与 `Runtime -> Host identity/re-entry adapter` 分开验证。当前会话工具清单、账号插件安装、生产 release/endpoint generation、Runtime ownership generation 分别取证。经 AI-Bridge 启动/诊断 LAB 属于迁移引导或显式救援，不属于 LAB Web 直连验收。

身份恢复沿用 `discover_current_entry` 与 Runtime canonical handoff；禁止把用户邮箱、active tab、URL、截图或本地 shell 请求当成本次 Web 调用来源证明。现存 `RESULT_UNKNOWN` 必须进入正式 reconciliation，不用直接重发或人工写 receipt 解锁。

### Computer Engine 接口演进边界

- 保留 Rust `ComputerRuntime` 的 capability/session/observation/ownership fence 和 Swift native backend；模型选择、视觉模型调用与任务规划位于外部 Agent/provider adapter。
- 结构化定位使用 AX locator；视觉定位返回目标框、语义描述、来源快照及可选置信度。两者都由本机执行层校验，不把模型输出当作可信授权。
- 为后续坐标动作定义一份可校验观察合同：session_id、expected_session_generation、observation_id、window_id、pid、captured_at/expires_at、image_sha256、image dimensions、window bounds、display identity/scale、coordinate transform、target bbox。具体 schema 随独立实现计划冻结，不宣称当前 API 已具备。
- 坐标变换必须处理窗口/桌面坐标、截图缩放、Retina 与多屏原点；动作前核对目标窗口和焦点、几何变化及遮挡。旧截图 hash 不等于新画面有效性；变化时要求新观察/重新定位。
- 动作开始与完成分开记录；超时/断连/崩溃后无法确定是否注入事件则返回不确定状态，不自动重复点击或键入。返回事件已发出不等于业务结果已成功。
- 支持用户抢占/紧急停止与有界执行；权限不足或目标不明确时 fail closed。不得触碰系统权限提示、复制登录凭据、模拟系统授权。

### 独立验证轨道

1. 身份/生产接入轨道：安装清单、状态、Connector、current-entry、正式 handoff、reconciliation、protected call、wake/CLOSED/acceptance 分门验收。
2. 离线可靠性轨道：Host、Bridge、production launcher、WEX 工具暴露回归；不因身份门阻塞而暂停。
3. Computer 轨道：现有原生动作补真实 fixture，再加入受约束坐标与可替换视觉定位；单独进行无旧 Helper / 无 Runtime 依赖测试。

每个轨道只在真实 dispatch/receipt 存在时标记已派发；没有可靠派发能力时可以顺序执行或并行运行独立测试，不伪称多 Agent 已在工作。产品最终稳定性通过故障注入、长时间运行和独立 review 证明。

## 2026-09-19：官方 CLI 协作面（飞书先行）技术合同

本条覆盖并取代 2026-09-18「v1 固定 10 个 feishu_* / 未知与 raw API fail-closed」技术合同。实现必须与 `SPEC.md` 同日产品条一致，不另开设计文档。

### 路径

- **Web / 远程 Agent**：`ChatGPT -> OpenAI MCP tunnel / Connector -> Local Tool Bridge -> LarkCliProvider -> official lark-cli -> Feishu Open Platform`。
- **Codex 桌面**：`Codex -> official lark-cli`（同一 `LARKSUITE_CLI_CONFIG_DIR` 与隔离 HOME）。桌面 MCP **不应**把 LAB Bridge HTTP 当作飞书网关。stdio 包装源文件：`scripts/feishu-provider/mcp-stdio.py`；本机运行副本在 `~/.local-agent-bridge/feishu-provider` 与 `~/.codex/bin/lark-cli-mcp`。
- 不经 CatDesk，不经 AI-Bridge 执行面。

`crates/collaboration-adapter` 只做进程契约：绝对路径 CLI、隔离环境、禁止 shell interpolation、超时与输出上限、结果分类。**不**为每个官方子命令维护一份 MCP schema。Named 工具是 Thin Slice 糖衣，官方 CLI 才是兼容层。

### Capability

- `CollaborationRead`：status、chat search/list、base list tables/fields/records、view list（落地后）。
- `CollaborationWrite`：base create/update record、message send/reply、create base、create view（后两项按 SPEC 下一刀落地）。
- `feishu_cli` 逃生口：按写能力授权（可触发写入）；拒绝 `auth` / `login` / `config` / `profile` / `update`。**禁止**在 generic 路径自动附加 `--format json`（`whoami` 等命令会炸）。有 named 工具的动作应走 named 工具。

### Web 工具面（有意做小）

当前应出现在 Connector `tools/list` 的飞书 named 工具：

`feishu_status`、`feishu_chat_search`、`feishu_message_list`、`feishu_base_list_tables`、`feishu_base_list_fields`、`feishu_base_list_records`、`feishu_base_create_record`、`feishu_base_update_record`、`feishu_message_send`、`feishu_message_reply`。

下一刀 named：`feishu_base_create`、`feishu_base_create_view`（`type` 含 `kanban`）。可选 `feishu_cli` 逃生口，不得替代上一列表成为 Web 主入口。

CatDesk/Host `tools/list` 失败时，飞书工具仍须出现（fail-open 飞书面），不得因 default backend 400 把整表打掉。`feishu_message_list` 对话题群将 `page_size` 提升到至少 20，避免空页。

不为「未知 feishu_*」制定产品级 fail-closed 教条；未实现的 named 工具返回普通 tool-not-authorized / not found。不把 117 个 `base +*` 注册进 MCP。

### 进程与发布

生产 CLI 路径绝对、真实、可执行；可选 dependency pin 版本/hash。凭据只在官方 CLI config/HOME，不进仓库、不进 MCP arguments。写入后超时 = `ResultUnknownAfterDispatch`，不自动 retry。Base 行写入保持 field-list preflight。

第二个官方 CLI：新开 Provider 配置与少量 named 工具，不先做通用注册表。官方新增用户不用的命令：忽略。官方改常用 named 包装的 flag：只改该包装。

### 与规划层的关系

`docs/product-planning-layer-design.md` 不承载飞书命令表。本协作面属于 LAB Tool Plane 的可插能力，普通调用不依赖 Adaptive Agent Runtime 在线。

## 开发执行面债务 / 根治方向

2026-09-18 实测暴露：现有 CatDesk-backed `start_command` 外层 sandbox 会阻断 `/dev/null`、部分 `/tmp`/用户目录 metadata，并把 PATH 压缩到系统目录；普通 Shell 同时按既定安全合同禁止 outbound network。结果是 Git/Cargo/npm 等开发工具无法作为可靠生产级开发执行面。

根治方向不是给普通 Shell 粗暴开网，而是：

1. 普通 Shell 继续保持无网络、workspace/环境隔离；
2. Provider/runtime process 逐步进入 LAB 自己的 capability-controlled process seam，减少对 CatDesk migration backend 的依赖；
3. 网络依赖安装独立成受控、manifest/allowlist 驱动的 Provider/Dependency Installer，明确 package/source/version/hash，不提供任意 URL/任意 shell；
4. 最终门禁必须重新证明 Git/local tool compatibility，同时保持 workspace 外写、任意网络、敏感 HOME 和外部同 UID signal 等原安全负例 fail closed。
