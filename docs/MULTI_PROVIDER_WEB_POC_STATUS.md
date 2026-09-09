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

| Surface | Official path | Live evidence | Real Web->LAB tool call | Status | Current blocker / next action |
|---|---|---|---|---|---|
| ChatGPT Personal Web | Secure MCP / Plugin reference path | mainline evidence | **PASS** | **REFERENCE PASS** | Owned by main Controller; do not duplicate |
| Claude Web | Official Remote MCP custom connector | Free account added and connected `LAB Safe PoC`; Claude discovered all 7 safe tools | **PASS**: read, `/bin/pwd`, write, edit, start job, poll terminal | **FULL PASS** | Productize authenticated ingress; temporary unauthenticated tunnel is PoC-only |
| Kimi Web | Official Plugin containing MCP | account logged in; Web plugin marketplace works; current official flow requires personal custom plugin registration through Kimi Work Plugin Builder | NOT YET | **NATIVE REGISTRATION GATED** | Kimi Work is not installed on this machine; create/register LAB personal plugin in Kimi Work, then invoke it from Kimi Web |
| Grok Web | Official Custom MCP connector | account logged in; Connectors and enabled `New Connector` button confirmed; docs specify `New Connector -> Custom -> MCP URL` | NOT YET | **HOST UI BLOCKED** | Current AI-Bridge browser click does not open Grok's Radix connector dialog even though DOM says enabled; not a provider capability failure |
| QwenWork Web | Official connector ecosystem / MCP-backed connectors | account logged in; Web connector marketplace verified; personal free Web UI exposes market/install only, no custom-MCP add control | NOT YET | **DESKTOP CONFIG GATED** | Official custom MCP setup is desktop-centric; QwenWork desktop is not installed here. Verify desktop-configured connector sync/use in Web |
| Gemini Spark | Custom Connected Apps via standard MCP URL | personal account logged in; English UI checked; no `Switch to Spark` or `Custom apps for Spark` surfaced | NOT YET | **ELIGIBILITY BLOCKED** | Current account does not expose Spark. Google requires Spark access before custom MCP can be used |
| Coze Agent | Official custom Streamable HTTP/SSE MCP | account logged in; custom `lab_safe_poc` saved; bound to default Agent; binding visible as `1 Agent` | NOT YET | **AGENT TOOLSET LOAD BLOCKED** | even after a new task, Agent runtime tool inventory omitted `lab_safe_poc`; registration/association passes but actual tool invocation does not |
| DeepSeek consumer Web | no official custom MCP established | account surface available | NOT RUN | **DEPRIORITIZED** | Do not browser-automate without explicit permission; API is separate |
| Doubao consumer Web | no direct official custom MCP established | not needed for representative official-MCP PoC | NOT RUN | **DEPRIORITIZED** | Prefer Coze official MCP ecosystem; do not infer Coze=Doubao Web |

## Claude live PASS evidence

Claude is the first non-ChatGPT Provider to complete the full safe Web -> LAB PoC in this research branch.

Observed path:

```text
Claude Web
  -> Anthropic Remote MCP
  -> temporary public HTTPS tunnel
  -> local LAB safe MCP server
  -> isolated PoC workspace/process
  -> result returned to Claude Web
```

Verified live:

- current Claude Free account exposes custom remote MCP connectors;
- `LAB Safe PoC` was saved and connected;
- Claude discovered four read-only and three write/delete safe tools;
- read-only calls require explicit approval under the selected connector policy;
- write/job calls also require explicit approval;
- `poc_command` executed fixed `/bin/pwd` and returned:
  `/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge/experiments/web-local-poc/web-safe-workspace`;
- `poc_write` succeeded in the isolated fixed target;
- `poc_edit` returned:
  `written-by-chatgpt-web-poc -> edited-by-chatgpt-web-poc`;
- `poc_start_job` returned job id `eb372b1e-8a11-4ecb-a14f-b6e605a53361`;
- `poc_poll_job` reached terminal `finished (code 0)` with `stdout: job-done`.

The successful public ingress used a temporary PoC tunnel only. It must not be treated as the production ingress/security design.

### PoC cleanup

After evidence collection:

- all temporary public tunnel processes were stopped;
- the fixed local safe PoC server remains bound only to `127.0.0.1:33302` as expected;
- the temporary Claude `LAB Safe PoC` connector was disconnected and removed;
- the Claude evidence chat was retained, but it no longer has the connector;
- Coze still displays the temporary `lab_safe_poc` account object/Agent association because its current management UI did not expose a reliable remove/detach action through the host bridge;
- that Coze object points to the now-dead temporary public URL, so it cannot reach the local LAB server.


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

Use official paths first and stop spending effort where the blocker is account/host tooling rather than provider feasibility:

1. productionize the provider-neutral authenticated HTTPS -> LAB ingress pattern learned from Claude;
2. Grok Web BYO MCP once the host browser can open its custom-connector dialog;
3. Kimi Work registration -> Kimi Web invocation;
4. QwenWork desktop custom MCP -> Web visibility/use;
5. Coze Agent toolset refresh/deploy behavior for an already associated custom MCP;
6. Gemini only when a Spark-eligible account is available.

One representative full PASS per integration family is enough before expanding breadth. The Claude full PASS already proves that a non-OpenAI consumer Web provider can invoke the shared LAB safe MCP over an official connector path.
