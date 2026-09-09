# Edict Design Review for LAB

> Source reviewed directly: https://github.com/Tse0227/edict
> Exact reviewed HEAD: 76e0c102ed1fd9bb541ec130fd1aa5eaa9300517
> License: MIT
> Checked: 2026-09-09
> Status: research candidate only.

## Verdict

Edict and LAB are **highly aligned in governance philosophy**, but not identical in collaboration topology.

Edict is strongest as a reference for:
- separation of powers;
- mandatory/explicit review gates;
- role/capability permissions;
- observable state machine;
- event-driven orchestration;
- durable event delivery/recovery;
- health/stall monitoring;
- real-time intervention;
- audit history.

However, current Edict deliberately rejects free Agent collaboration and enforces a hierarchical relay path. Its own Agent protocol says collaboration requests go through the dispatch authority rather than cross-department direct connection.

LAB should borrow Edict's **institutional safeguards**, but not copy its fixed hierarchy.

## 1. What matches LAB strongly

### Separation of concerns/powers

Edict separates:
- intake/triage;
- planning;
- review/veto;
- dispatch;
- execution.

LAB already has analogous concepts:
- Controller;
- decomposition/DAG;
- Reviewer;
- routing/delegation;
- Executors.

The useful idea is not the historical role names; it is that planning, review, dispatch, and execution are not one unchecked authority.

### Permission matrix

Edict configures explicit allowed Agent relationships and rejects unauthorized Agent-to-Agent invocation.

LAB should generalize this into capability-scoped peer permissions, dynamically tied to:
- task;
- workspace/worktree;
- role;
- governance profile;
- provider/host;
- risk class.

### Event-driven runtime

Edict's newer architecture defines an Event Bus and uses Redis Streams consumer groups/ACKs. The code explicitly recovers stale pending events after worker crashes.

Useful LAB concepts:
- durable event delivery;
- consumer ACK;
- unacked recovery;
- trace/task correlation;
- live Pub/Sub separate from durable stream.

LAB should reuse the **semantics**, not necessarily require Redis. LAB is local-first and can implement a lighter durable log initially.

### Observability and intervention

Edict makes task state, progress, heartbeats, stalls, cost, stop/cancel/resume visible.

This is highly aligned with LAB's need to make long-running Agent work trustworthy.

### Hard review/veto

Edict's reviewer can reject a weak plan and force replanning.

LAB should keep the stronger generalized form:
- reviewer independence;
- candidate acceptance;
- integration/current-main evidence;
- profile-dependent mandatory review.

## 2. Where Edict is too centralized for LAB's target

Edict's normal flow is a fixed hierarchy and its docs explicitly prohibit cross-department direct communication.

This produces a strong control model but creates exactly the bottleneck the LAB design now wants to remove:
- central dispatch becomes a message router;
- peer questions require relay;
- conflict resolution climbs the hierarchy;
- every task pays governance overhead;
- fixed roles do not map cleanly to arbitrary Providers/hosts.

LAB should not copy:
- mandatory fixed hierarchy for every task;
- hardcoded historical roles;
- one dispatch authority in every peer exchange;
- mandatory review for trivial low-risk tasks;
- routing based solely on role name/state.

## 3. Edict + A2A synthesis

Best synthesis:

```text
Edict institutional governance
  +
A2A open Agent interoperability
  +
LAB provider-neutral Runtime / identity / local Tool Plane
  =
LAB Adaptive Agent Mesh
```

Edict contributes:
- rules;
- review;
- permissions;
- visibility;
- recovery.

A2A contributes:
- Agent discovery;
- messages;
- tasks;
- artifacts;
- streaming;
- cross-vendor interoperability.

LAB contributes:
- Web + desktop + local host bridges;
- provider routing;
- canonical project state;
- execution identity;
- local Files/Shell/Jobs/Browser/Computer;
- governance profile selection;
- cross-provider recovery;
- authoritative acceptance.

## 4. Borrowing map

| Edict mechanism | LAB action |
|---|---|
| permission matrix | BORROW, generalize to capability/task scoped graph |
| mandatory reviewer | BORROW as G3 policy; do not force on G0/G1 |
| task state machine | BORROW concepts, retain LAB DAG/lifecycle |
| Redis Streams ACK | BORROW delivery/recovery semantics |
| stale event claim | BORROW for peer message consumer recovery |
| flow/progress audit | BORROW into durable Agent Mesh timeline |
| heartbeat/stall | BORROW and merge with LAB lease/progress health |
| stop/cancel/resume | BORROW normalized controls |
| visible Kanban | BORROW UX concepts |
| per-Agent model | BORROW, extend to Provider/host/capacity routing |
| fixed Three Departments/Six Ministries | DO NOT COPY |
| no cross-department direct communication | INVERT: allow policy-scoped A2A |
| one central dispatcher for all collaboration | DO NOT COPY |
| mandatory heavy process for all tasks | REPLACE with adaptive governance profiles |

## 5. Improvements LAB should make over Edict

### Policy graph instead of fixed org chart

```text
Edict:
role A -> allowed role B

LAB:
agent A -> permission edge -> agent/task/resource B
conditional on governance/risk/workspace
```

### Escalation ladder

Direct peer resolution first:
1. peer negotiation;
2. independent reviewer/arbitrator;
3. Controller;
4. user.

Do not start at step 3 for ordinary conflicts.

### Durable critical outcome

A chat exchange is not the authoritative result.

Every important negotiation ends in a durable artifact/receipt:
- resolution;
- review result;
- handoff;
- accepted interface contract;
- blocker/escalation.

### Adaptive governance

Edict treats institutional review as universal. LAB should choose governance depth based on task/risk.

### Cross-provider/host identity

Edict assumes agents inside one OpenClaw environment. LAB must handle:
- Web conversation identity;
- desktop sessions;
- local daemons;
- multiple Providers;
- endpoint leases;
- reconnect/re-entry.

## 6. Implementation caution

The Tse0227/edict repo is MIT licensed, so implementation ideas/code can be evaluated for reuse with license notice obligations.

But do not import its OpenClaw-specific configuration/runtime assumptions into LAB core.

Particularly:
- `allowAgents` can inspire policy schema but should not become the canonical LAB model;
- Redis should remain an implementation option rather than a product dependency;
- OpenClaw CLI dispatch is an adapter pattern, not the LAB executor contract.

## 7. Proposed product language

Old:
> Controller centrally dispatches and coordinates all Agents.

New:
> LAB provides adaptive governance for an Agent Mesh. Agents collaborate directly across hosts and Providers within capability-scoped policy; the Controller remains accountable for canonical state, risk boundaries, escalation, and final acceptance where required.

That preserves responsibility without turning the Controller into a throughput bottleneck.
