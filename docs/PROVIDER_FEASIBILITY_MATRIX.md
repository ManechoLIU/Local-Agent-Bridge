# PROVIDER_FEASIBILITY_MATRIX

> Checked: 2026-09-09
> Status: research candidate only; not a declaration of formal LAB product support.
> Ratings: GO / LIKELY / UNCERTAIN / NO-GO. “Commercial” means supportability for a third-party LAB product under currently visible provider terms, not legal advice.

## Executive matrix

| Provider / surface | Direction A: Provider -> LAB | A commercial | Direction B: LAB -> Provider | B commercial | Capacity observability | Recommended next step |
|---|---|---|---|---|---|---|
| ChatGPT Web | REFERENCE / mainline | Mainline gate | REFERENCE / WEX mainline | Mainline gate | provider UI / plan dependent | Do not duplicate here |
| Claude Web | **GO** via official remote MCP custom connector | **LIKELY GO** for connector use | **LIKELY** technically via browser, but not preferred | **NO-GO absent explicit permission** for consumer browser automation | 5h session reset + weekly cap; exact usage is dynamic | First international A PoC after ChatGPT PASS |
| Gemini Spark | **GO, constrained**: custom MCP only inside Spark tasks | **LIKELY GO** within documented feature | **LIKELY** technically; official Gemini-in-Chrome agent surface also exists | **UNCERTAIN** for third-party browser-driving consumer UI | compute-based dynamic quota; up to 15 concurrent Spark tasks | Research provider-supported Spark/Chrome seam before browser adapter |
| Grok Web | **GO** via official BYO MCP; official docs explicitly support tunneling local MCP | **LIKELY GO** within connector terms | **LIKELY** technically; Grok Bot is a stronger provider-supported agent surface | Consumer browser automation **UNCERTAIN**; Grok Bot business use **LIKELY GO** subject to agreement | paid shared weekly pool; free limits separate; exact remaining may be UI-dependent | Highest-priority international dual-direction PoC candidate |
| Kimi consumer Web | No official custom MCP found: **UNCERTAIN** | **UNCERTAIN** | Browser control technically **LIKELY** | **NO-GO absent permission**: current Kimi terms prohibit scripts/bots simulating manual automated/bulk use | monthly shared credits; official UI exposes balance % + next refresh | Do not build consumer Web Adapter; evaluate Kimi Code separately |
| Kimi Code CLI | **GO** MCP client (stdio/HTTP/SSE) | **LIKELY GO** within Kimi Code terms/plan | **GO/LIKELY** as provider-supported programmatic executor, but **not Web** | **LIKELY GO** subject to Kimi Code commercial terms | shared credits + separate 5h/week Code limit | Adjacent optional executor, not Web mainline |
| DeepSeek consumer Web | No official custom MCP found: **UNCERTAIN** | **UNCERTAIN** | Playwright/CDP PoCs prove **LIKELY** technical feasibility | **NO-GO/high risk absent permission**: terms restrict automated capture/copy via robots/spiders/automatic setups | consumer Web exact quotas not reliably published -> **UNKNOWN** | Do not prioritize consumer Web adapter |
| DeepSeek API/Open Platform | Not the Web subscription path | API terms | **GO** as API executor | **GO/LIKELY** under developer agreement | API usage/price observable | Keep as optional BYO API executor, not Web differentiator |
| Qwen consumer Web | No direct qwen.ai custom MCP found: **UNCERTAIN** | **UNCERTAIN** | Browser automation technically **LIKELY** | **NO-GO**: current qwen.ai terms prohibit automated interaction/extraction/download incl Outputs | consumer Web allowance -> **UNKNOWN** unless UI exposes it | Do not build consumer Web Adapter |
| Qwen Code | **GO** official MCP client | **LIKELY GO** | **GO/LIKELY** programmatic/CLI executor, but **not Web** | **LIKELY GO** subject to Qwen Code terms | product-specific | Adjacent optional executor |
| Doubao consumer Web | Direct custom MCP not established: **UNCERTAIN** | **UNCERTAIN** | Open-source Playwright/CDP proves **LIKELY** technical feasibility | **UNCERTAIN / HIGH-RISK**: consumer license is personal/non-commercial; unauthorized access/use restrictions apply | paid quota refreshes monthly; units vary by feature; exact allowance is UI-defined | Avoid browser Adapter until explicit commercial permission |
| Coze / 扣子 Web & Desktop Agent | **GO** official custom MCP over Streamable HTTP/SSE | **LIKELY GO** under applicable Coze plan | Provider-supported Agent workflows **GO/LIKELY**, but not the same as Doubao consumer Web | **LIKELY GO** subject to plan/enterprise agreement | documented plan call limits for some MCP/plugin surfaces | Strong China representative for official MCP PoC |
| Doubao via Coze publish channel | Historical path only | n/a | **NO-GO for new publication** | n/a | n/a | Coze docs say new publishing to Doubao channel was removed 2026-07-01 |

## Provider notes

### ChatGPT

This is the LAB reference implementation and remains owned by the mainline Controller. This research workstream does not re-run Host/WEX acceptance.

Current OpenAI docs show custom MCP/apps are first-party product concepts, but plan/surface details are evolving. The project must keep its existing live evidence as authority for the reference path and fresh-check OpenAI plan capability at release time.

### Claude

**Direction A — GO.** Claude supports custom connectors using remote MCP on Claude Web and other Claude clients. Free/Pro/Max/Team/Enterprise are documented; Free is limited to one custom connector. Calls to remote MCP originate from Anthropic cloud, so a LAB tunnel/gateway is architecturally appropriate.

**Direction B — technical LIKELY, commercial NO-GO absent permission.** A browser adapter is ordinary engineering, but Anthropic Consumer Terms prohibit automated/non-human access except via API key or where explicitly permitted. Therefore LAB must not commercialize consumer Claude Web as an automated executor unless Anthropic explicitly authorizes that surface.

Best product interpretation:
- Claude Web -> LAB: official connector;
- LAB -> Claude: prefer an explicitly permitted provider surface/API/partner agreement, not browser-driving consumer claude.ai.

### Gemini

**Direction A — GO but constrained.** Google supports custom Connected Apps via MCP, currently only for Gemini Spark users and inside Spark tasks. This is not yet equivalent to “ordinary Gemini chat supports custom MCP everywhere.”

Google also has first-party **Gemini in Chrome auto browse**, including multi-step actions across websites and signed-in browsing for eligible users. This is important evidence that Google has an official Web-agent surface, but it is **not equivalent to LAB local execution**. Auto browse operates browser tasks; it does not by itself establish a general Files / Shell / Process / Jobs / Computer Runtime plane on the user's machine.

For LAB, Gemini auto browse is therefore best modeled as an **upstream Web Agent surface that LAB can complement**, not as a substitute for LAB. A future combined path could be: `Gemini Spark / Gemini in Chrome -> LAB Tool Plane -> local files/shell/jobs/computer + optional cross-provider dispatch`. The research priority is to find a provider-supported handoff from Gemini/Spark into LAB (for example MCP/custom connected app surfaces) before considering a gemini.google.com browser adapter.

**Direction B — technical LIKELY, commercial UNCERTAIN.** Community Playwright wrappers demonstrate feasibility, but browser-driving Gemini consumer UI is not the strategic reason to support Gemini. The stronger product story is that LAB extends Gemini beyond Web browsing into local execution and multi-provider orchestration. Keep commercial browser-automation status UNCERTAIN until a specific permission/partner surface is identified.

### Grok

**Direction A — GO.** Grok Web has official connectors and Bring Your Own MCP. SpaceXAI documentation explicitly describes tunneling a local MCP server to a public URL, which closely matches LAB’s Web->Local architecture.

**Direction B — LIKELY.** Consumer Grok terms explicitly describe Grok’s own Agentic Actions and rate limits but do not, from this source audit, grant a third-party right to browser-drive consumer Grok. Mark consumer browser automation commercial status UNCERTAIN.

A stronger path exists: **Grok Bot** terms explicitly support internal business use and autonomous actions such as websites, code, files and tool invocation. Treat Grok Bot as a distinct provider-supported executor surface and prioritize it over an unofficial consumer browser adapter.

### Kimi

Kimi Code is architecturally friendly: official MCP client support includes stdio, HTTP and SSE. That is a **provider-supported adjacent surface**, not proof that ordinary Kimi consumer Web has custom MCP.

Current Kimi consumer terms prohibit automated scripts/programs/tools (including bots/crawlers/scheduled tasks) that simulate manual operation for automated or bulk use. Therefore:
- consumer Web browser Adapter: technical LIKELY, commercial NO-GO absent permission;
- Kimi Code: GO/LIKELY adjacent executor/tool client.

Community Kimi browser projects are useful only as technical evidence. Do not copy credential/cookie handling patterns into LAB.

### DeepSeek

No official custom MCP capability for ordinary DeepSeek consumer Web was established in this audit.

Community Playwright/CDP wrappers show LAB->DeepSeek Web is technically feasible. Current DeepSeek terms restrict capturing/copying Service content using robots, spiders or other automatic setups. That makes a commercial consumer-Web executor high-risk/NO-GO absent explicit permission.

DeepSeek’s official API/Open Platform remains a clean optional BYO API route, but it does not satisfy LAB’s Web-subscription differentiator.

### Qwen

Qwen Code has official MCP support (HTTP/SSE/stdio), making it an attractive adjacent provider-supported surface.

For qwen.ai consumer Web, current Terms of Service prohibit automated interaction, extraction or download of Service information/content including Outputs, and separately prohibit scraping/mining/distilling via scripts/tools/agents. Therefore consumer Web browser execution is commercial NO-GO absent explicit permission even if technically easy.

### Doubao / Coze

Ordinary Doubao consumer Web has community browser wrappers, proving technical feasibility. Some community implementations explicitly advertise bypassing anti-bot controls; those patterns are unacceptable for LAB and must never be used.

Doubao’s current user agreement grants a personal, revocable, non-transferable, non-exclusive and **non-commercial** license and restricts unauthorized access/use. A third-party commercial browser executor therefore remains UNCERTAIN/HIGH-RISK without a separate permission or agreement.

The ByteDance ecosystem has a much stronger official path through **Coze / 扣子**:
- Web/Desktop Agent supports custom MCP;
- custom MCP supports Streamable HTTP and SSE;
- Coze can expose or consume MCP-based capabilities in documented product flows.

However, Coze’s official update log says the route for newly publishing low-code agents to the Doubao channel was removed on **2026-07-01**. Do not infer “Coze MCP = Doubao consumer Web MCP.”

## Capacity policy

Do not treat any consumer subscription as unlimited.

Runtime-facing state is only:

\`\`\`text
AVAILABLE
THROTTLED
LIMIT_REACHED
UNKNOWN
\`\`\`

Optional reset timestamps or quantitative remaining allowance may be surfaced only when the provider exposes them reliably. Dynamic caps, model-specific caps, fallback models and concurrent-task caps must remain provider facts, not guessed percentages.

## Initial PoC priority after CHATGPT_LAB_REFERENCE_PASS

Recommended representative sequence based on official integration strength + product differentiation + commercial risk:

1. **Grok Web -> LAB BYO MCP** — strongest structural match to LAB tunnel model.
2. **Claude Web -> LAB remote MCP** — mature official connector, broad consumer-plan availability.
3. **Gemini Spark -> LAB custom MCP** — official but constrained; validates Google ecosystem.
4. **Coze Agent -> LAB custom MCP** — strongest current China official-MCP representative.
5. **Grok Bot as LAB executor** — provider-supported outbound/agent surface candidate.
6. **Kimi Code / Qwen Code** — adjacent official executor/tool surfaces; useful for architecture, but not Web subscription proof.

Do **not** prioritize consumer browser adapters for Claude, Kimi, DeepSeek or Qwen while current terms remain restrictive.

## Source audit

Official sources checked:
- OpenAI Apps in ChatGPT: https://help.openai.com/en/articles/11487775-connectors-in
- OpenAI developer mode/MCP: https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt
- Claude remote MCP connectors: https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp
- Anthropic Consumer Terms: https://www.anthropic.com/legal/consumer-terms
- Gemini Connected Apps: https://support.google.com/gemini/answer/13695044
- Gemini in Chrome auto browse: https://support.google.com/gemini/answer/16821166
- Grok connectors: https://x.ai/news/grok-connectors
- Grok connector docs: https://docs.x.ai/grok/connectors
- Grok MCP tunneling: https://docs.x.ai/grok/connectors/custom-mcp-tunneling
- Grok Consumer Terms: https://x.ai/legal/terms-of-service
- Grok Bot Terms: https://x.ai/legal/grok-bot-terms
- Kimi Code MCP: https://moonshotai.github.io/kimi-code/en/customization/mcp.html
- Kimi terms: https://www.kimi.com/user/agreement/modelUse?version=v2
- DeepSeek Terms: https://cdn.deepseek.com/policies/en-US/deepseek-terms-of-use.html
- Qwen Terms: https://qwen.ai/termsservice
- Qwen Code MCP docs: https://github.com/QwenLM/qwen-code-docs/blob/main/website/content/en/users/features/mcp.md
- Doubao User Agreement: https://www.doubao.com/legal/terms
- Coze MCP: https://docs.coze.cn/mcp
- Coze update log: https://docs.coze.cn/recent-updates
- Capacity research appendix: `docs/provider-capacity-routing.md`

Technical-only community evidence (never a commercial permission source):
- Gemini Playwright wrapper: https://github.com/lesterppo/hermes-gem-pw
- DeepSeek Playwright MCP: https://github.com/xihe-lab/deepseek-mcp-server
- Doubao Playwright/CDP examples: https://github.com/qiukui666/doubao-web-image and https://github.com/Xian-debu/DoubaoCLI
- Kimi WebBridge MCP examples: https://github.com/AkagiYui/kimi-webbridge-mcp

## Evidence policy

- Official documentation/terms outrank community PoCs for support classification.
- Community PoCs may upgrade TECHNICAL_FEASIBILITY only.
- A community PoC can never upgrade COMMERCIAL_SUPPORTABILITY.
- Claims involving anti-bot bypass, cookie extraction, private API reversal or usage-limit circumvention are negative evidence for LAB implementation suitability.
- Re-check sources immediately before a Provider enters formal PoC because terms/features change rapidly.
