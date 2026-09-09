# Kimi Code / Qwen Code Reuse & LAB Differentiation

> Checked: 2026-09-09. Research candidate only; not authoritative until accepted by the unique LAB Controller.

## Direct MCP reuse

Kimi Code and Qwen Code are MCP clients. Both can consume LAB as an MCP server.

Preferred path:

Provider Code Agent -> stdio/Streamable HTTP MCP adapter -> LAB signed UDS ingress -> LAB Tool Plane -> Files/Shell/Process/Jobs/Browser/Computer Runtime

Do not point provider agents around LAB directly at CatDesk. Provider-side trust/allowlists are additional controls, not replacements for LAB capability, workspace, signing, and audit enforcement.

Effective permission = provider MCP permission AND LAB capability AND LAB workspace policy AND tool-specific approval gates.

## Transport fit

- Kimi Code: stdio, Streamable HTTP, SSE.
- Qwen Code: stdio, SSE, Streamable HTTP.
- LAB current hardened local ingress: 0600 Unix-domain socket with signed per-request capability proof.
- Therefore add only a thin stdio or loopback-HTTP -> UDS adapter; do not redesign the Tool Plane.

## Subscription/service restrictions

Open-source runtime code and model-service entitlement are separate.

- Kimi Code subscription guidance allows interactive use in supported coding/agent tools, but forbids non-interactive scripted/batch automation and resale/repackaging as a service; commercial integrations are directed to Kimi Platform.
- Alibaba Cloud Coding Plan is for interactive AI programming tools and explicitly forbids automated scripts, application backends, and other non-interactive use.
- Qwen Code authentication can instead use general API/Token Plan/other providers; terms depend on the chosen model service.

Implication: LAB Connect may use interactive user-owned coding-agent surfaces after exact terms review. LAB Projects unattended/commercial execution must use a model-service plan that explicitly allows that mode.

## Correction: they are not just 'hands'

Kimi Code now has:
- autonomous /goal across continuing turns;
- pause/resume/cancel/replace and queued follow-up goals;
- resumable persistent sessions;
- todo/plan state;
- background tasks;
- subagents / Agent Swarm;
- Skills and Plugins.

Kimi Work goes further with Projects, Goal mode, scheduled tasks, Agent Swarm, local files/browser/code execution, and documented long-horizon work.

Qwen Code now has:
- /goal with independent completion/blocking verifier;
- evidence archival and budget-aware handoff;
- background subagent continuation;
- Agent Team with persistent teammates/shared task lists;
- checkpointing;
- durable scheduled loops across restart;
- workflows;
- qwen serve long-lived workspace runtimes with reconnect-safe SSE.

Therefore LAB must not claim competitors lack long-project execution.

## LAB differentiation target

LAB should compete one level above a single-provider coding/local agent:

| Capability | Kimi/Qwen today | LAB target |
|---|---|---|
| Local files/shell | strong | strong, provider-neutral |
| MCP tools | strong clients | shared Tool Plane across providers |
| Goal/long tasks | yes | yes |
| Subagents | yes | yes |
| Session resume | yes | yes |
| Durable scheduling | available in Qwen/Kimi Work | yes |
| Independent verifier | Qwen Goal has one | formal Reviewer + acceptance gates |
| Multi-provider routing | model-provider support inside one local agent | first-class Web/local/API executor routing |
| Existing Web AI -> Local | not core definition | core LAB Connect feature |
| Cross-provider child agents | limited/agent-specific | core runtime feature |
| Capacity-aware provider routing | limited | core target |
| Authoritative project ledger/DAG/READY/WIP | not equivalent to LAB governance | core |
| Logical Controller across Web/session/host | no equivalent established | core |
| Authenticated exactly-once Web re-entry | different scope | core |
| Candidate->Reviewer->integration->current-main acceptance | workflow dependent | canonical runtime gate |
| Shared Browser/Computer Runtime for all providers | provider runtime specific | unified Tool Plane |

## What to absorb

From Kimi Code:
- MCP allow/block + permission composition;
- provider protocol abstraction;
- resumable event-stream sessions;
- Goal follow-up queue;
- subagent timeout/settlement;
- plugins bundling Skills + Agents + MCP;
- ACP/local Server API control surface.

From Qwen Code:
- safe MCP replay using trust + idempotent/read-only annotations;
- progressive MCP discovery;
- independent Goal verifier/evidence archival;
- budget-aware Goal handoff;
- persistent Agent Team + shared task list;
- shadow-Git checkpointing;
- durable scheduler ownership/failover;
- reconnect-safe SSE replay;
- qwen serve workspace daemon architecture.

Current source licensing:
- MoonshotAI/kimi-code: MIT.
- Qwen Code: Apache-2.0.

Permissive source licensing can allow code/architecture reuse subject to attribution/license obligations. It does not grant model-service quota or commercial usage rights.

## Recommended low-cost PoC after CHATGPT_LAB_REFERENCE_PASS

1. Add a thin LAB stdio or loopback-HTTP MCP adapter over signed UDS.
2. Connect Kimi Code to LAB; expose controlled read/write/shell/job first.
3. Connect Qwen Code to the same LAB endpoint.
4. Verify provider trust cannot override LAB denial.
5. Verify timeout/cancel/error mapping.
6. Add accurate MCP readOnly/idempotent/destructive annotations and test disconnect replay.
7. Separately test Kimi ACP/Server API and qwen serve as optional local/provider-supported executors.

Product position:

Kimi Code / Qwen Code = strong local coding-agent runtimes.
Kimi Work = strong single-provider general local Agent.
LAB = provider-neutral Web AI Local Execution + shared Computer Runtime + cross-provider orchestration + durable project governance/acceptance/recovery.
