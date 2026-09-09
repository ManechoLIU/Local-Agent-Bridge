# Multi-Provider Architecture Constraints

> Status: research candidate only. Non-authoritative until accepted by the unique LAB Controller.
> Priority: non-blocking until the ChatGPT reference implementation reaches its formal pass gate.

## Product definition

LAB is not a ChatGPT-specific bridge.

Long-term product definition:

```text
LAB = Web AI Local Execution + Multi-Agent Runtime
```

The durable differentiation is not merely browser automation. Provider-native agent features and LAB solve different layers.

## Capability planes

Keep these planes explicit for every Provider:

| Plane | Meaning |
|---|---|
| `WEB_AGENT` | Provider can browse/act on websites or provider-connected SaaS surfaces |
| `WEB_TO_LAB` | Provider-owned Web/Agent surface can call LAB tools through an official/supported connector path |
| `LAB_TO_PROVIDER` | LAB Runtime can create/bind/drive a provider execution and receive a correlated terminal result |
| `LOCAL_EXECUTION` | Provider can reach the user's controlled local Files/Shell/Process/Jobs/Computer Runtime through LAB |
| `CROSS_PROVIDER` | Initiating provider can delegate through LAB to other provider executors |
| `PROJECT_ORCHESTRATION` | Long-running Goal/DAG/READY/WIP/reviewer/recovery/continuation governance |

A Provider having `WEB_AGENT=YES` does **not** imply `LOCAL_EXECUTION=YES`.

Example:

```text
Gemini Spark / Gemini in Chrome
  -> official Web browsing / Connected Apps / remote browser or computer
  != LAB local-machine execution plane

Gemini Spark
  -> custom MCP
  -> LAB Tool Plane
  -> local Files / Shell / Process / Jobs / Browser / Computer Runtime
  -> optional Claude / Grok / other Provider dispatch
```

This is a complementary relationship, not a claim that LAB duplicates Gemini's browser agent.

## One Tool Plane

All Providers share the same LAB Tool Plane:

```text
ChatGPT Web ─┐
Claude Web ──┤
Gemini Web ──┤
Grok Web ────┤
Kimi Web ────┤
DeepSeek Web ┤
Qwen Web ────┤
Doubao Web ──┘
             ↓
        LAB Tool Plane
             ↓
Files / Shell / Process / Jobs / Browser / Computer Runtime
```

Adding a Provider must not create a provider-specific Local Bridge.

## Two directions are separate products contracts

### Direction A — Provider Web -> LAB

Question:

> Can a user who is already working in this Provider's Web/Agent product invoke LAB local capabilities through an official or supportable integration?

Preferred order:
1. official MCP / connector / tool interface;
2. provider-supported integration;
3. supported authenticated agent surface;
4. browser adapter only if terms and reliability pass;
5. experimental research fallback.

### Direction B — LAB -> Provider

Question:

> Can Adaptive Agent Runtime dispatch an Assignment into an actual provider execution, correlate identity/result/terminal, and continue the SAME Controller lifecycle?

A Provider may be:
- A=GO, B=NO-GO;
- A=NO-GO, B=GO;
- both GO;
- both technically feasible but commercially blocked.

Never collapse A and B into one “supports LAB” boolean.

## Provider-neutral executor rule

Runtime owns:
- assignment/execution lifecycle;
- result and terminal correlation;
- capacity state;
- routing;
- acceptance/reviewer policy;
- recovery and continuation policy.

ProviderAdapter owns:
- account/session;
- provider surface selection;
- conversation/task creation;
- provider-specific model selection;
- prompt/task submission;
- completion observation;
- result extraction;
- provider-specific session expiry/reload;
- provider-specific capacity/limit signals.

Runtime must not understand Provider DOM, login labels, consumer model-menu wording, or quota UI.

## Capability-advertised Adapter

Do not require fake parity.

An Adapter may advertise:

```text
create_execution
submit
observe
extract_result
identity
capacity

optional:
model_select
reasoning_select
cancel
resume
reload_recover
provider_tools
web_to_lab_connector
```

Unsupported operations remain explicitly unsupported.

## Capacity-aware routing

Only normalized states are authoritative:

```text
AVAILABLE
THROTTLED
LIMIT_REACHED
UNKNOWN
```

Optional exact reset or remaining amounts are allowed only with reliable provider evidence.

Never:
- infer a fake percentage;
- rotate accounts to bypass limits;
- retry indefinitely through a provider cap;
- hide a provider limit by silently switching user identity.

## Commercial classification

Every surface gets an independent implementation class:

```text
OFFICIAL
PROVIDER_SUPPORTED
BROWSER_ADAPTER
EXPERIMENTAL
```

and two verdicts:

```text
TECHNICAL_FEASIBILITY
COMMERCIAL_SUPPORTABILITY
```

A browser proof only answers technical feasibility.

## Product modes

### LAB Connect

User remains in ChatGPT / Claude / Gemini / Grok / Kimi / DeepSeek / Qwen / Doubao or another provider surface.

LAB supplies:
- local tools;
- computer use;
- cross-provider delegation;
- temporary multi-agent collaboration.

### LAB Projects

LAB adds durable governance:
- Controller;
- Goal;
- DAG;
- READY/WIP;
- Provider routing;
- parallel agents;
- independent review;
- acceptance;
- recovery;
- continuation.

Multi-Provider collaboration is not Projects-only. Projects add durable governance.

## Current implementation discipline

Before `CHATGPT_LAB_REFERENCE_PASS`:
- keep Multi-Provider non-blocking;
- freeze contracts;
- maintain source-audited matrix;
- permit only cheap GO/NO-GO spikes;
- do not implement seven or eight full adapters.

After `CHATGPT_LAB_REFERENCE_PASS` and core M0 stability:
- promote `MULTI_PROVIDER_POC` to READY;
- fresh-read provider features/terms;
- select the highest-value official/supportable surface;
- run one minimal Provider PoC;
- decide GO/NO-GO before the next Provider.

## First representative directions

Current research preference, subject to fresh re-check at promotion time:

International:
1. Claude Web -> LAB official remote MCP (current Free test account exposes custom connector UI);
2. Grok Web -> LAB official BYO MCP;
3. Gemini Spark -> LAB custom MCP when eligibility is present;
4. Grok Bot / provider-supported outbound agent surface.

China:
1. Kimi Web -> LAB official Plugin/MCP on supported Kimi Web surfaces;
2. QwenWork -> LAB custom MCP after exact Web-vs-desktop availability verification;
3. Coze Agent -> LAB custom MCP;
4. Kimi Code / Qwen Code as adjacent native runtimes;
5. consumer browser adapters only when the exact consumer surface passes an explicit commercial gate.

This sequence validates architectural diversity without starting all Providers simultaneously.
