# Provider Commercial Gate

> Research candidate. This is an engineering/product gate, not legal advice.

## Purpose

A Provider becomes a formal LAB product surface only when both gates pass:

\`\`\`text
TECHNICAL_FEASIBILITY = GO
AND
COMMERCIAL_SUPPORTABILITY = GO
\`\`\`

A technically functioning browser adapter is not sufficient.

## Support classes

| Class | Meaning | Product treatment |
|---|---|---|
| OFFICIAL_CONNECTOR | Provider explicitly exposes a connector/MCP/tool integration to the relevant Web/Agent product | Preferred |
| PROVIDER_SUPPORTED_EXECUTOR | Provider explicitly exposes an agent/API/CLI/business surface suitable for automated execution | Preferred outbound path |
| BROWSER_ADAPTER | LAB drives an authenticated provider Web UI | Beta only after explicit commercial/terms gate |
| EXPERIMENTAL | Reverse-engineered/unstable/research-only | Never default product support |

## Hard commercial checks

For every provider/surface record:

1. **Automation permission**
   - Is non-human/browser/script access explicitly allowed, prohibited, or silent?
   - Does an API/official agent exception exist?

2. **Consumer subscription scope**
   - Personal/non-commercial only?
   - Internal business use permitted?
   - Third-party product use permitted?

3. **Credential/session handling**
   - Can LAB reuse a user-owned authenticated browser session without exporting credentials?
   - Does the candidate require cookies/access tokens to be extracted or copied?
   - If yes, reject that implementation pattern.

4. **Redistribution/resale**
   - LAB sells orchestration/local execution, not provider model quota.
   - Never resell, pool or transfer a user’s subscription/credentials.
   - Never share one account across unrelated users.

5. **Usage limits**
   - No rate-limit bypass.
   - No anti-bot bypass.
   - No account rotation to defeat caps.
   - Provider limits become routing facts.

6. **Branding and representation**
   - Do not imply partnership, endorsement or “official support” where only a browser adapter exists.

7. **Data handling**
   - Minimize provider content persisted by LAB.
   - Do not persist raw credentials.
   - Separate provider conversation/result retention from LAB execution receipts.

## Current commercial gate snapshot

| Surface | Gate |
|---|---|
| Claude Web official remote MCP | LIKELY GO |
| Claude consumer browser executor | NO-GO absent explicit Anthropic permission |
| Gemini Spark custom MCP | LIKELY GO within documented Spark scope |
| Gemini consumer browser executor | UNCERTAIN |
| Grok Web BYO MCP | LIKELY GO |
| Grok consumer browser executor | UNCERTAIN |
| Grok Bot business/agent surface | LIKELY GO subject to customer agreement |
| Kimi Web official Plugin/MCP | LIKELY GO within documented model/scenario surface; revalidate plan/region |
| Kimi consumer browser executor | NO-GO absent permission |
| Kimi Code | LIKELY GO as adjacent official surface |
| DeepSeek consumer browser executor | NO-GO / high risk absent permission |
| DeepSeek API | GO under developer terms, but not Web-subscription route |
| qwen.ai consumer browser executor | NO-GO absent permission |
| QwenWork custom MCP/Connector | LIKELY GO within documented product surface; exact Web custom-MCP availability still verify |
| Qwen Code | LIKELY GO as adjacent official surface |
| Doubao consumer browser executor | UNCERTAIN / high risk |
| Coze custom MCP/Agent | LIKELY GO within plan/terms |

## Implementation red lines

Formal LAB code must not adopt a candidate that depends on:
- exporting or scraping browser cookies;
- stealing/accessing private provider tokens;
- reverse-engineered private APIs as the primary production path;
- defeating CAPTCHA/risk control automatically;
- bypassing anti-bot systems;
- cycling accounts or sessions to evade quota;
- falsifying remaining quota;
- silently falling back to a different user/provider account.

A research PoC that demonstrates any of these may be retained only as **negative feasibility evidence**, not as reusable implementation.

## BYO subscription model

LAB’s commercial value is:

\`\`\`text
Local Execution
+ Computer Runtime
+ Multi-Agent orchestration
+ automation/recovery
+ security
+ projects/device management
\`\`\`

It is **not** resale of ChatGPT/Claude/Gemini/Grok/Kimi/DeepSeek/Qwen/Doubao model quota.

Users bring their own provider account/subscription. LAB must preserve provider-imposed limits and permissions.

## Revalidation trigger

Before promoting any Provider from research to formal Adapter:
- fresh-read current provider terms;
- fresh-read plan/feature availability;
- confirm the exact surface being automated;
- distinguish consumer Web from API/CLI/business agent;
- run independent commercial/source review;
- record version/date of the terms used.

Any material terms or product-surface change resets COMMERCIAL_SUPPORTABILITY to UNCERTAIN until re-reviewed.
