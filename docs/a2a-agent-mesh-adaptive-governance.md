# LAB A2A Agent Mesh & Adaptive Governance

> Checked: 2026-09-09
> Status: research candidate only. Non-authoritative until accepted by the unique LAB Controller.
> Goal: remove Controller-as-message-broker bottlenecks while preserving authoritative project governance.

## 1. Principle: decentralize collaboration, not authority

LAB should separate two planes:

```text
Collaboration Plane
  Agent <-> Agent
  question / proposal / evidence / conflict / handoff / review request
  direct, cross-host, cross-provider

Control Plane
  authoritative project state
  ownership / DAG / acceptance / integration / risky side effects
  governed by policy + reviewer/controller/user gates
```

The Controller should become a **governance authority and escalation endpoint**, not the mandatory relay for every message.

An Agent may communicate directly with another Agent, but direct communication does not automatically grant authority to:
- mutate canonical project state;
- change ownership;
- merge main;
- override another Agent's workspace;
- bypass an acceptance gate;
- perform a higher-risk side effect.

## 2. Use A2A as the wire protocol

LAB should align its cross-Agent communication surface with the Agent2Agent (A2A) protocol rather than inventing a competing wire format.

Current A2A 1.0 concepts map well to LAB:

| A2A | LAB mapping |
|---|---|
| AgentCard | AgentEndpoint capability/identity advertisement |
| Message | negotiation, question, clarification, peer communication |
| Task | delegated unit of work |
| Artifact | durable result/evidence/diff/review output |
| contextId | collaboration channel / task context correlation |
| streaming | live Agent progress |
| push notifications | disconnected/long-running Agent updates |
| CancelTask | bounded cancellation |
| security schemes | Agent endpoint authentication |
| protocol extensions | LAB governance/conflict metadata |

A2A deliberately lets independent/opaque Agents collaborate without exposing internal memory or tools. That is desirable for cross-provider LAB.

LAB should add **A2A extensions**, not fork the core protocol.

Suggested extension namespaces:
- `urn:lab:governance:v1`
- `urn:lab:conflict:v1`
- `urn:lab:evidence:v1`
- `urn:lab:execution-identity:v1`
- `urn:lab:capacity:v1`

## 3. AgentEndpoint

Every active Agent surface is registered as an endpoint independent of provider/host:

```text
agent://chatgpt/web/<id>
agent://codex/desktop/<id>
agent://claude/web/<id>
agent://grok/web/<id>
agent://kimi/code/<id>
agent://qwen/code/<id>
agent://gemini/web/<id>
```

Normalized facts:

```text
agent_id
provider
host_type
surface
session_id?
conversation_id?
execution_id?
role
task_ids[]
workspace/worktree?
capabilities
security_requirements
governance_profile
lease/health
capacity
status
```

Provider-specific adapters own how an endpoint is reached. Agent Mesh owns communication semantics.

## 4. Cross-host communication

Example:

```text
ChatGPT Web Agent
      |
      | A2A Message
      v
LAB Agent Mesh / durable message plane
      |
      +----> Desktop Codex Agent
      +----> Claude Web Agent
      +----> Grok Web Agent
      +----> Kimi Code Agent
      +----> Qwen Code Agent
```

The Controller is not in the message path unless policy requires escalation.

For Providers that do not expose A2A natively, LAB supplies an A2A Adapter over the best supported control surface:
- Web Host/Provider Adapter;
- ACP;
- MCP-backed agent control;
- local daemon/server;
- provider-supported business agent/API.

## 5. Adaptive governance profiles

Governance should scale with task difficulty and risk.

### G0 — Direct

For trivial, low-risk, single-Agent tasks.

Characteristics:
- one Agent;
- no mandatory planner/reviewer;
- bounded local tool permissions;
- direct result.

Example: summarize a file, harmless read-only query.

### G1 — Delegated

For moderate tasks where one Agent owns the result and may delegate.

Characteristics:
- primary Agent may call peers directly;
- lightweight task ownership;
- optional peer review;
- no mandatory central relay;
- acceptance can be local if low-risk and evidence is strong.

Example: small code change with one specialist consultation.

### G2 — Mesh Collaboration

For multi-Agent, multi-module, or cross-provider work.

Characteristics:
- peer-to-peer A2A channels;
- explicit task/worktree ownership;
- direct conflict negotiation;
- independent Reviewer when needed;
- durable messages/evidence;
- Controller only handles policy, escalation, acceptance rules.

Example: frontend/backend agents with shared interface conflict.

### G3 — Strong Governance

For high-risk, release, security, irreversible, cross-project, or ambiguous work.

Characteristics:
- explicit planning;
- separation of planning/review/execution;
- mandatory independent review;
- stricter ownership/generation fencing;
- canonical acceptance gate;
- higher-risk side effects require explicit authority;
- Controller/arbitrator becomes active when local negotiation cannot safely decide.

Example: auth/security migration, production release, destructive data operation.

### Promotion rules

Runtime may automatically **escalate** governance when it detects:
- shared-file/worktree conflicts;
- security/release scope;
- irreversible side effects;
- uncertain previous side-effect result;
- multiple failed peer-resolution rounds;
- cross-project changes;
- reviewer disagreement;
- provider/session instability;
- long-lived task drift.

Do not automatically downgrade below a user/project-required profile.

## 6. Direct conflict resolution protocol

A code conflict should not automatically become a Controller conversation.

```text
Agent A detects conflict
  -> conflict.open
  -> Agent B

A <-> B exchange:
  intent
  touched files/symbols
  invariants
  evidence
  candidate resolution

if agreed:
  -> conflict.resolution artifact
  -> affected Agent(s) apply/verify
  -> reviewer/acceptance according to governance profile

if unresolved:
  -> peer arbitrator / reviewer

if still unresolved or high-risk:
  -> Controller/user escalation
```

Suggested structured metadata:
- conflict_id;
- task_ids;
- participants;
- files/symbols/resources;
- base revision;
- intents;
- invariants;
- proposed resolution;
- evidence refs;
- round;
- deadline;
- escalation policy.

This prevents the Controller from becoming a human-style message copier.

## 7. Direct Agent permissions

Permission should be capability-scoped rather than role-title hardcoded.

Examples:

```text
may_message(agent|task|channel)
may_request_review(task)
may_propose_state_change(task)
may_accept_subtask(task)
may_edit(scope)
may_merge(branch)
may_spawn(peer_class)
may_delegate(task_scope)
may_escalate()
```

A peer communication grant is not a mutation grant.

Example:
- Codex Agent may directly ask ChatGPT Architect about an interface.
- It may not automatically change ChatGPT's owned design artifact.
- Both may create a joint resolution artifact.
- Integration occurs through the current governance profile.

## 8. Message reliability

Critical facts must not rely on transient chat messages.

A2A itself distinguishes Messages from Artifacts and warns that transient messages are not guaranteed to be reliable history. LAB should therefore persist critical outcomes as durable records/artifacts.

Suggested delivery layers:
- durable event/message log;
- per-message ID;
- task/context correlation;
- ACK/delivery state;
- deduplication;
- bounded retry;
- unknown-delivery classification;
- replay from cursor/receipt;
- expiry for noncritical chatter.

Do not conflate:
- message redelivery;
- task retry;
- tool side-effect retry.

These need separate idempotency rules.

## 9. Canonical state boundary

Agents can freely discuss **proposed facts**.

Only authorized operations can change canonical facts.

```text
peer message
  -> proposal/evidence
  != canonical ledger mutation

authorized state transition
  -> receipt
  -> validated ownership/generation
  -> canonical ledger update
```

This keeps decentralized collaboration from producing split-brain project state.

## 10. Controller role after decentralization

Controller becomes:

- policy owner;
- governance-profile selector/overrider;
- canonical-state guardian;
- conflict escalation/arbitration endpoint;
- final acceptance authority where required;
- routing/recovery authority for project-level failure.

Controller should no longer be:
- mandatory mailbox;
- mandatory task-status relay;
- mandatory peer-question router;
- mandatory conflict discussion participant.

Target: **centralized accountability without centralized communication traffic**.

## 11. Product experience

### LAB Connect

A Web Agent can:
- use LAB local tools;
- discover other eligible Agents;
- ask another provider for a second opinion;
- open an A2A collaboration channel;
- receive an Artifact back into the initiating Web conversation.

### LAB Projects

Adds:
- durable task graph;
- agent ownership;
- adaptive governance profiles;
- conflict channels;
- reviewer/acceptance rules;
- project-wide recovery and provider switching.

## 12. Initial implementation sequence

Do not block current ChatGPT reference pass.

After reference stability:

1. freeze AgentEndpoint + A2A mapping;
2. implement local Agent Mesh registry;
3. implement durable peer Message/Artifact store;
4. expose A2A-compatible local endpoint;
5. add Codex desktop <-> ChatGPT Web first cross-host test;
6. add direct conflict channel + resolution artifact;
7. add governance profile G0-G3;
8. add one non-OpenAI provider;
9. test multi-provider peer negotiation;
10. only then expand dynamic discovery and remote A2A.

## 13. Success criteria

A2A Agent Mesh is successful when:

- two Agents on different hosts can communicate without Controller relaying;
- two Providers can negotiate on one task;
- conflict resolution is durable/auditable;
- direct communication cannot mutate unauthorized canonical state;
- agent crash/reconnect does not lose critical resolution artifacts;
- unresolved conflicts escalate automatically;
- simple tasks avoid heavyweight governance;
- high-risk tasks automatically retain strong review/acceptance.
