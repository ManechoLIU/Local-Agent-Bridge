# 产品规范

> 产品目标、非目标、行为、流程、状态和验收以此为准。当前只记录用户已明确确认的产品级约束；未确认细节不得擅自补全。

## 目标

- 建立新的 **Local Agent Bridge / Local Tool Bridge**，让 ChatGPT Web 或其他 Agent 能安全调用本机 Files、Shell、Process、Browser 与独立 Computer Runtime。
- Bridge 与 Computer Runtime 可以被 Adaptive Agent Runtime 调用，但产品必须支持不启动任何项目治理 Runtime 的独立运行模式。
- 建立机器可验证的 execution-session identity / host-session attestation / capability verification / execution-target identity，最终淘汰当前 manual Web Controller binding。
- 将 **Web Host Attestation + Re-entry Adapter** 纳入正式产品范围：由真实 browser/Host machine boundary 为 exact ChatGPT Web conversation/execution target 生成 freshness/anti-replay attestation，并为 Adaptive Agent Runtime 提供可选 verifier + SAME-Controller re-entry/wake 能力；该 adapter 与 Local Tool Bridge / Computer Runtime 解耦。
- 在安全边界成立后，提供足以支持真实开发工作的读写、进程、浏览器和桌面执行能力。

## 非目标

- 不把 Adaptive Agent Runtime、SelfAlone Controller、Goal 或 TASK_LEDGER 设计成 Bridge / Computer Runtime 的产品运行依赖。
- 不复用 SelfAlone 的 project identity、logical Controller 或 lifecycle。
- 不把 prompt 中的 conversation ID、随机 URL path、`Mcp-Session-Id`、进程存活、页面 DOM/标题/截图或**单纯 URL** 当作 host-attested identity；browser navigation machine observation 只有在同时绑定 browser target/document identity、fresh challenge、generation/ownership fence 与 Host-signed receipt 时才可进入 attestation verifier。
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
- Web Host Attestation + Re-entry Adapter 必须真实证明：machine-verifiable exact conversation target、fresh challenge/nonce、target generation、ownership generation、Host provenance/receipt 与 anti-replay；`manual_user_authorized`、`ui_observed conversation_id`、tab/URL/title 单独均不得升级 VERIFIED。
- Same-controller Live-E2E 必须完成 `existing Web Controller -> child/executor terminal -> Runtime canonical continuation -> Host-attested same target -> automatic re-entry/submit -> Controller fresh-read + project-wide recompute -> next runnable`，全程不依赖用户再发“继续”。
- Host attestation/re-entry Harness 必须覆盖 stale target、stale generation、wrong conversation、duplicate/replay、temporary disconnect、transient re-entry unavailable、supervisor restart；失败路径 fail closed。

## v1 本机威胁模型

- v1 必须防御来自 ChatGPT/Web/Remote tunnel/prompt 的越权请求、capability 重放/过期/撤销绕过、workspace hardlink/symlink/path escape、Shell filesystem/network/environment escape，以及其他 OS 用户通过私有状态/UDS 直接接入。
- v1 **不声称**能够防御已经以与 Bridge 相同 macOS 用户身份运行的恶意本地进程。same-UID 恶意进程可读取/调试该用户进程或访问该用户本就有权访问的数据，因此 0600 UDS 不是这类对手的强隔离边界。
- 当前临时 CatDesk loopback backend 也只在上述 v1 边界内成立：same-UID 恶意进程可尝试绕过 Local Tool Bridge 直接访问本机 backend。正式文档与 UI 不得把当前方案描述成 same-UID malware isolation。
- 若未来把 same-UID hostile process 纳入安全目标，必须升级为独立 OS principal / sandbox worker、受认证的 backend peer，或把 Files/Shell/Jobs backend 直接并入 Local Tool Bridge，从结构上移除可绕过的同 UID backend。

## 2026-09-17 用户确认：LAB 主路径迁移与自有 Computer Engine

### 产品结果与优先级

用户已确认：日常本机操作默认通过 Local Agent Bridge；AI-Bridge 保留为显式应急兜底，不自动或静默回落。优先修复/核验当前总控身份绑定，并尽快完成当前登录账户的 LAB 接入。身份链阻塞不能暂停不依赖 Controller 权限的产品文档、离线测试及隔离开发。

登录账户、浏览器 profile、Web conversation、Bridge execution session、logical Controller 是不同身份。账户登录成功不得被描述成总控已绑定。沿用唯一项目 Controller；只通过正式 Host/Runtime 接口进行 fresh attestation 与 ownership/generation 校验，不手工编辑注册表、回执或 CLOSED/acceptance 状态。

### 第一阶段：LAB Connect 迁移

目标路径：`ChatGPT Web / other agent -> supported connector/tunnel -> Local Agent Bridge -> Files / Shell / Process / Jobs -> local machine`。

- 明确展示服务就绪、Connector 安装、当前回合工具暴露、实际执行来源四个不同状态。服务在线不等于当前聊天已接入。
- 迁移期间不卸载、不停掉唯一仍可用的救援通道；只有 LAB 直连实际验收通过后，才在可恢复的独立测试中验证 AI-Bridge OFF。
- 完成真实文件读写编辑、受约束命令、任务成功/失败/取消/超时及重连测试；执行证据必须指向 LAB，而不是经 AI-Bridge shell 转发后声称已经完成迁移。
- 普通 Files/Shell/Jobs 独立运行不得被总控生命周期或项目治理服务强制阻塞。Controller 专用保护配置可以要求可信 Runtime hook，但不能代替普通独立产品配置。
- 请求发送后的不确定结果不自动重放；旧 wake 必须先 reconciliation。只有签名确认未送达才允许新的 distinct wake。

### 第二阶段：替代外部 Computer Helper

在现有 `crates/computer-runtime` 上演进自有 Computer Engine，不新造重复 runtime。采用 **Accessibility/结构化 UI 优先 + 视觉定位兜底 + 受约束坐标执行**。

结构化信息不足时，允许接入外部 Agent/可替换视觉 provider 对截图定位；不要求首版内置本地 OCR 或自研视觉模型，也不把特定模型供应商写入本机执行核心。模型置信度只能辅助判断，不能替代授权或身份校验。

闭环：`observe -> AX grounding -> optional visual grounding -> bounded action -> fresh observe -> outcome verification`。

视觉/坐标动作必须绑定 session/generation、observation、window ID + PID、截图 hash、截图与桌面坐标变换、目标框及 TTL；动作前重验窗口/焦点/几何/遮挡等必要条件，拒绝过期、跨窗口、超界及来源不明坐标。截图 hash 仅标识观察快照，不能证明当前屏幕未变。

原生授权仍由用户通过系统界面授予；不得操作系统权限提示、绕过 macOS 隐私控制或把视觉兜底当作绕过权限的方法。默认不得操作登录凭据、系统安全授权弹窗等敏感目标；高风险动作按产品授权策略停下。用户手动输入或窗口漂移时应停止/重新观察，不能盲点重试。

### 分阶段验收

| 阶段 | 用户可观察结果 | 通过条件 |
| --- | --- | --- |
| 身份优先 | 当前工作入口可被正式验证 | machine current-entry + 唯一 Controller + 当前 ownership/generation；之后单独验证 confirmed wake、post-wake CLOSED、accept-live-e2e |
| LAB Connect | 当前账户/聊天实际调用 LAB | Connector 与生产工具清单一致；真实 Files/Shell/Jobs 调用成功且无 AI-Bridge 执行依赖 |
| 原生桌面基础 | 自有引擎操作 harmless fixture | 真实 screenshot/find/click/type/key/scroll、权限撤销与 stale target 负例；独立 review |
| 混合视觉 | 无 AX 控件也能安全定位与操作 | Canvas/自绘 fixture、缩放/Retina/多屏坐标、窗口移动/遮挡/焦点竞争、越界/TTL/重放拒绝、动作结果复核 |
| Helper 退役 | 自有执行面不依赖旧 Helper | 外部 Helper OFF 的独立 E2E；故障有分层诊断、显式恢复和可审计来源 |

以上是已确认的目标/验收合同，不表示已经实现或通过。稳定性优于旧方案的结论需要同负载、同环境的成功率、延迟、超时/恢复、误操作与拒绝率数据支持。

## 2026-09-19 用户确认：官方 CLI 协作面（飞书先行）

本条覆盖并取代此前未提交的「v1 固定 10 个 feishu_* / 未知 fail-closed」合同。产品真相只保留这一份。不在 `docs/product-planning-layer-design.md` 再写一套飞书方案；规划层管的是 Planning vs Runtime，以及模型 Provider，不是飞书命令表。

### 要解决的问题

用户要在 ChatGPT Web 和 Codex 桌面使用**同一份已登录官方 CLI**（当前为 `lark-cli@1.0.96`，账号「哈喽」）。Web 到不了本机，必须经 LAB；桌面已在本机，不应再绕 LAB。不把官方 CLI 的全部子命令做成 MCP 工具；不为「官方可能大改 / 将来很多三方 CLI」现在建设通用插件总线。

### 产品结果

- **一次登录**：官方 CLI 的 config / 隔离 HOME 只维护一份；禁止产品路径再发起 `auth login`。
- **Web**：ChatGPT Connector / 隧道 → Local Agent Bridge → 官方 CLI。Web 工具面以**少量 named 工具**为主，让模型能完成真实任务（搜群、读消息、读/写已有多维表格；下一切片含创建 Base 与看板 view）。
- **桌面**：Codex 直连同一官方 CLI 登录态，不把 LAB Bridge 当作桌面飞书网关。LAB 不可用时，桌面飞书仍应可用。
- **逃生口**：允许一个 `feishu_cli`（argv 交给官方 CLI），仅用于尚未做成 named 工具的命令。有专用工具时不得作为主入口。禁止用它做第二次 OAuth。
- **扩展（现在不做框架）**：第二个官方 CLI 真正出现时，再复制「隔离 HOME + 禁止 login + 少量 named 工具 + 可选 argv 逃生口」。不预先做多 Provider 注册表。

### 非目标

- 把 `lark-cli` 全部 `+` 命令映射为 MCP 工具。
- 桌面 MCP 必须经 LAB 才能用飞书。
- 现在建设通用 `external_cli` 总线或企微/钉钉包装。
- 经 AI-Bridge `office_im` 作为正式飞书路径。
- 静默把「本机 CLI 已登录」说成 Web/桌面会话已接通。

### 当前 Thin Slice（按使用频率，不按 CLI 目录完整性）

1. 登录探测：`feishu_status`
2. 群：`feishu_chat_search`、`feishu_message_list`；发送/回复仅在用户明确要说话时使用
3. 已有多维表格：list tables/fields/records；create/update **一行**记录（写入前 schema preflight）
4. 下一刀：创建 Base、创建 view（含 `type=kanban`）——仍是 named 工具，不是 argv 主路径

群聊自动发送默认 bot；user identity 需显式。写操作发出后结果不确定则 `result_unknown_after_dispatch`，不自动重试。话题群消息列表不得使用会返回空页的过小 `page_size`（实测 <20 会空）。

### 授权与验收

- 凭据不得进入 MCP arguments、普通日志或模型可见输出。
- Web：隧道或 Connector 的 `tools/list` 含上述 named 工具；真实 `feishu_status` 与至少一次 `feishu_chat_search` 或 `feishu_message_list` 成功。本机 HTTP 成功不能代替 Web 隧道证据。
- 桌面：新会话使用同一 `LARKSUITE_CLI_CONFIG_DIR` / 隔离 HOME，不要求经 `127.0.0.1:18765`。
- 创建 Base/看板在该切片落地前标未做，不得称为已接全 CLI。
