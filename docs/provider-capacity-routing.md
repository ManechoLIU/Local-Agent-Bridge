# Provider Capacity & Routing Research

> Checked: 2026-09-09
> Status: research candidate only. Provider limits are fast-changing and must be fresh-read before formal PoC or release.

## Normalized Runtime state

Runtime uses only evidence-backed states: `AVAILABLE`, `THROTTLED`, `LIMIT_REACHED`, `UNKNOWN`.

Never derive a fake remaining percentage from retry count or latency.

## Current official findings

| Provider / surface | Current official limit model | Machine-routing implication |
|---|---|---|
| Claude consumer | Usage depends on conversation length/complexity/model/features. Pro has at least 5x Free session usage; session reset every 5 hours; Pro/Max also have weekly limits and may have other model/feature caps. | Expose reset/limit-hit only when provider evidence is reliable; never assume fixed message count. |
| Gemini Apps | Compute-based limits vary by prompt complexity/model/feature/chat length. Refreshes every 5 hours until weekly limit. | Treat quota as dynamic; use provider warnings/limit UI as evidence. |
| Gemini Spark | Consumes Gemini compute-based allowance. Maximum 15 concurrently running tasks. | `concurrency_hint=15` applies only to Spark tasks, not general Gemini concurrency. |
| Gemini in Chrome auto browse | Pro: up to 20 multi-step requests/day; Ultra: up to 200/day in current auto-browse docs. | These limits belong only to auto browse; do not generalize to Spark MCP or ordinary chat. |
| Grok consumer paid | SuperGrok paid products draw from one shared weekly usage pool; paid features pause at weekly exhaustion. Free Chat/Voice limits remain separately available. | A paid pool can be exhausted while a lower/free surface still exists; fallback must be explicit. |
| Grok Free | Free plan has lower limits; public pricing does not expose a stable numeric allowance. | Numeric remaining is `UNKNOWN` unless product exposes it at runtime. |
| Kimi membership | Agent/Deep Research/PPT/Docs/Sheets/Kimi Code/Kimi Work/Kimi Claw share one credit pool; credits refresh monthly. Official UI can show balance percentage and next refresh. | Percentage may be surfaced only when read from official account UI. |
| Kimi Code | Shares Kimi credits and has an additional 5-hours-per-week limit. | Track both shared credit state and Code-specific weekly state. |
| Doubao paid | Paid AI features consume quota derived from token/service use; quotas refresh monthly and unused quota expires; units may be counts or duration. | Use official subscription UI; do not assume stable unit conversion across features. |
| DeepSeek consumer Web | No reliable public fixed Web allowance established in this audit. | Default `UNKNOWN`; only classify limit state from direct provider evidence. |
| Qwen consumer Web | No reliable public fixed Web allowance established in this audit. | Default `UNKNOWN`; do not infer from browser failures. |
| Coze | Limits are plan/feature specific; MCP/tool use can increase token/credit consumption. | Surface-specific observation required. |

## Routing behavior

- `LIMIT_REACHED`: stop retries for that provider/surface, preserve evidence, recompute route.
- `THROTTLED`: back off within provider policy; consider another eligible provider if routing permits.
- `UNKNOWN`: do not treat as unlimited; allow only a normal bounded attempt.
- `AVAILABLE`: eligibility still depends on task suitability, commercial support class, concurrency, reviewer independence, and user preference.

## Fallback rules

Fallback is valid only when the user owns/has access to the fallback provider, route policy allows it, the commercial gate passes, and switching does not evade a provider restriction.

Never cycle accounts, reopen sessions to defeat cooldowns, use private endpoints to avoid limits, or silently substitute API usage for a Web-subscription route.

## Official sources

- Claude usage limits: https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work
- Claude Pro: https://support.claude.com/en/articles/8325606-what-is-the-pro-plan
- Claude Max: https://support.claude.com/en/articles/11049741-what-is-the-max-plan
- Gemini Apps limits: https://support.google.com/gemini/answer/16275805
- Gemini Spark: https://support.google.com/gemini/answer/17094507
- Gemini in Chrome auto browse: https://support.google.com/gemini/answer/16821166
- Grok FAQ: https://docs.x.ai/grok/faq
- Grok pricing: https://x.ai/pricing
- Kimi credits: https://www.kimi.com/en/help/agent/agent-quota-and-billing
- Kimi membership: https://www.kimi.com/en/help/membership/membership-overview
- Doubao paid services: https://www.doubao.com/legal/ey01

## Revalidation

Before each formal Provider PoC, fresh-read official limits, record plan/surface, record whether remaining allowance is machine-observable, and reset capacity to `UNKNOWN` when the provider materially changes its quota model.
