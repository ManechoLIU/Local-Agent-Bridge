# Multi-Provider Web PoC Status

> Checked: 2026-09-09
> Research candidate only; mainline acceptance remains owned by the unique LAB Controller.

## Definition of PASS

A Provider Web -> LAB PoC is only PASS when an authenticated provider surface actually invokes a controlled LAB MCP tool and the local result is independently observed. Official docs or a visible connector UI are not enough.

Minimal target sequence where the surface permits it:

1. authenticated Web/Agent surface;
2. official/supportable MCP/connector path;
3. LAB discovery;
4. controlled read;
5. controlled write/edit where provider approvals allow;
6. harmless command;
7. start/poll short job;
8. local evidence + provider result.

## Current status

| Surface | Official path | Local MCP preflight | Live provider UI/account | Real Web->LAB tool call | Status | Current blocker / next action |
|---|---|---|---|---|---|---|
| ChatGPT Personal Web | Secure MCP / Plugin reference path | PASS | PASS | **PASS** in mainline evidence | **REFERENCE PASS** | Owned by main Controller; do not duplicate |
| Claude Web | Remote MCP custom connector | PASS using existing standard-MCP safe PoC server on 127.0.0.1:33302 | **PASS**: current Free account exposes Add custom connector + remote MCP URL field | NOT YET | **READY / INGRESS BLOCKED** | Current network/VPN blocks temporary public tunnel handshakes; retry with working public HTTPS ingress |
| Kimi Web | Official Plugin containing MCP | reusable standard MCP server ready | **Web opened; not signed in** | NOT YET | **ACCOUNT + INGRESS GATED** | User login is required; then confirm supported K3/K3 Swarm/plugin surface and invoke LAB through official plugin/MCP |
| Grok Web | Official custom MCP connector; official tunneling docs | reusable standard MCP server ready | **Connectors page opened; not signed in** | NOT YET | **ACCOUNT + INGRESS GATED** | User login + working public HTTPS ingress |
| QwenWork | Official custom MCP client/connector | reusable server; may use remote HTTP or local STDIO depending surface | not checked in this cycle | NOT YET | **SURFACE VERIFY** | Confirm whether custom MCP can be added/used directly from Web or must be configured in desktop first |
| Gemini Spark | Custom Connected Apps via standard MCP URL | reusable standard MCP server ready | current Gemini account is logged in, but Spark access not established | NOT YET | **ELIGIBILITY GATED** | Verify Spark access, US/personal account/English/Keep Activity conditions, then run PoC |
| Coze Agent | Official custom MCP | reusable standard MCP server ready | not checked in this cycle | NOT YET | **POC READY BY PRODUCT** | Account/surface live E2E |
| DeepSeek consumer Web | no official custom MCP established | n/a | n/a | NOT RUN | **DEPRIORITIZED** | Do not browser-automate without explicit permission; API is separate |
| Doubao consumer Web | no direct official custom MCP established | n/a | n/a | NOT RUN | **DEPRIORITIZED** | Prefer Coze official MCP ecosystem; do not infer Coze=Doubao Web |

## Claude live attempt evidence

This cycle verified all preconditions except public reachability:

- existing LAB safe PoC MCP server responds to standard initialize on 127.0.0.1:33302;
- the server exposes only fixed isolated tools and does not expose arbitrary project paths/shell;
- logged-in Claude Free UI exposes Settings -> Connectors -> Add -> Add custom connector;
- the modal asks for Name and Remote MCP server URL over HTTPS;
- no connector was saved and no account configuration was mutated;
- localhost.run and Pinggy SSH tunnels stalled under the current network path;
- Cloudflare Quick Tunnel allocated a URL but edge TLS negotiation failed with EOF; temporary processes were cleaned up.

This is **not a Claude failure**. It is a local network/public-ingress blocker.

## Fresh product corrections

### Kimi

Official Kimi Help now states that Kimi Plugins may contain MCP and that Kimi Web currently supports MCP and Skills in plugins. Availability is constrained to documented models/scenarios. This upgrades Kimi Web from UNCERTAIN to an official Provider->LAB PoC candidate. Browser-driving Kimi consumer Web remains a separate restricted surface.

### Qwen

Qwen must be split into independent surfaces:

- qwen.ai consumer Web;
- QwenWork;
- Qwen Code.

Official QwenWork docs define QwenWork as an MCP client and document Streamable HTTP/SSE/STDIO custom MCP configuration. Web docs confirm connectors broadly, but the custom MCP setup guide is desktop-specific; exact Web custom-MCP behavior remains a live-verification item.

## Next PoC order

Use official paths first and do not implement browser adapters where an official connector exists:

1. finish Claude Web once public ingress is reachable;
2. Kimi Web Plugin/MCP;
3. Grok Web BYO MCP;
4. QwenWork exact Web/desktop connector behavior;
5. Gemini Spark if current account is eligible;
6. Coze Agent.

One representative PASS per integration family is enough before expanding breadth. Do not require every Provider to repeat every security proof already owned by the shared LAB Tool Plane; Provider PoCs prove provider transport/tool invocation compatibility, while LAB security remains a shared product gate.
