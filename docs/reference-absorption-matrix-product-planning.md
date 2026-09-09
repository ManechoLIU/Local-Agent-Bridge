# LAB Reference Absorption Matrix — Product Planning vs Runtime

> Reviewed 2026-09-09.
> Research candidate only.

## Summary

| Source | Primary lesson | Absorb into | Do not copy |
|---|---|---|---|
| 得物小摊 Delivery Harness | facts/boundaries/evidence/feedback must control delivery state | Planning↔Runtime contract + Runtime evidence gates | team-specific implementation details |
| 腾讯大仓 Harness | SPEC-First; requirement understanding before solution/code; specialized roles only where useful | Product Planning Layer | fixed 13 stages / fixed 4 Agents |
| dsh-alpha | multi-machine Agent inventory, canonical workspace identity, reverse gateway, durable task recovery, approval forwarding | Agent Mesh / Host Fabric / Runtime executor routing | central master as mandatory collaboration relay |
| nullurl/adaptive-delivery | risk-trimmed delivery governance, unique facts, ledger, A2A/recovery, verification | Adaptive Agent Runtime | using Runtime as product discovery engine |
| nullurl/Marvin-thinking-os | evidence-first discovery, key decision, Thin Slice, workflow evidence, value hypothesis | Product Planning Layer | unrelated consulting/commercial/FDE ceremony by default |

## 1. 得物小摊 — Delivery Harness

Useful concepts identified in the article:
- Version Contract;
- Execution Boundary;
- Evidence Gate;
- Repair Loop.

### Absorb

**Version Contract → planning/runtime handoff**
- product behavior must be versioned before broad execution;
- repository/branch/runtime facts should not be guessed;
- confirmed facts and temporary inference remain separate.

**Execution Boundary → Runtime**
- worktree/repository scope;
- external writes/publish boundaries;
- no undeclared cross-system expansion.

**Evidence Gate → Runtime and planning readiness**
- “implemented” ≠ “verified” ≠ “product accepted” ≠ “released”;
- every state transition needs evidence appropriate to that claim.

**Repair Loop → bidirectional Planning↔Runtime feedback**
- real defects and runtime discoveries should improve defaults;
- only verified learning becomes a future rule.

### Do not copy

- no new Version Contract database;
- no separate evidence system if existing Runtime receipts can express it;
- no duplicate source-of-truth documents.

## 2. 腾讯大仓 Harness

Most valuable lessons:
- SPEC-First precedes Agent architecture;
- their retained roles are Requirements, Solution, Development, Review;
- Requirements Agent converts issue/PRD into structured understanding + code impact map;
- Solution Agent writes executable design;
- Development creates implementation plus interface verification;
- Review checks solution consistency, acceptance coverage, and quality baseline.

### Absorb

Planning Layer:
- structured requirement understanding;
- impact map as generated evidence, not product truth;
- explicit product-to-solution transition;
- planning review before expensive implementation.

Runtime:
- test/verification as part of the producer's work;
- review should consume real verification evidence.

### Do not copy

- fixed 13-stage workflow;
- mandatory 4 Agents for every task;
- team/tool-specific TAPD/iWiki/Git host integrations in core.

Principle:
**retain responsibilities, not organization chart.**

## 3. dsh-alpha

Exact repository reviewed:
- `songofhawk/dsh-alpha`
- reviewed HEAD: `9d39b0328f9ee53c201610dac65e852e5c64237a`

Strong patterns:
- global machine/Agent/workspace inventory;
- logical repository identity separated from local paths;
- routing by machine/provider/model/capability/load;
- reverse WebSocket Workers;
- durable task IDs;
- reconnect/replay;
- per-Worker allowed roots;
- approval forwarding;
- same task continues after control-session interruption.

### Absorb

Agent Mesh / Host Fabric:
- endpoint catalog;
- canonical workspace identity;
- host affinity;
- load/capability routing;
- outbound Worker connection to avoid public host exposure;
- approval forwarding;
- durable task/event correlation.

Runtime:
- dispatch idempotency;
- heartbeat/lease;
- event replay/dedup.

### Do not copy

- one master conversation as permanent authority for all collaboration;
- DSH-specific workspace/session assumptions;
- CLI adapters as core contract.

Our A2A design should allow peers to communicate directly while central governance only handles policy/escalation/canonical state.

## 4. nullurl/adaptive-delivery

Exact repository reviewed:
- `nullurl/adaptive-delivery`
- reviewed HEAD: `cf624155484d0549c6036bb798b576e81938e989`

It already covers:
- quick/standard/strict risk trimming;
- SPEC/DESIGN/TECHNICAL/TASK_LEDGER responsibility separation;
- long-task governance;
- A2A persistent task control;
- context governance;
- validation evidence;
- recovery;
- project initialization.

### Keep in Runtime

This remains the closest reference for:
- execution governance;
- unique task ledger;
- long-running work;
- Agent ownership;
- evidence/review;
- recovery.

### Extract only one pre-runtime boundary

Add a stronger `PLANNING_READY` contract.

Do not expand Adaptive Runtime into:
- customer discovery;
- product strategy;
- market research;
- full product planning.

## 5. nullurl/Marvin-thinking-os

Exact repository reviewed:
- `nullurl/Marvin-thinking-os`
- reviewed HEAD: `9ce4495a433094fb46b5d99cdabcca253e9201a8`

Useful planning patterns:
- distinguish confirmed fact / reported statement / interpretation / unknown;
- start from real workflow rather than “which AI tool can we use?”;
- identify one key decision;
- confirm resource/owner constraints;
- value hypothesis before implementation;
- Thin Slice before platform scope;
- `role → input → action → judgment → output → downstream` workflow evidence card;
- keep assumptions visible;
- do not promote one successful prototype into a general rule.

### Absorb

Planning Layer:
- evidence taxonomy;
- problem/owner/success-signal discipline;
- workflow evidence card;
- Thin Slice;
- value hypothesis;
- readiness judgement;
- unknown/conflict tracking.

### Do not copy by default

- consulting-specific diagnosis package;
- pricing/SOW machinery;
- enterprise ontology;
- FDE production governance;
- GTM/commercial protocols.

These are optional domain skills, not LAB core.

## 6. Net-new capability required by LAB

The five sources together suggest one missing seam:

```text
Product Intent
   ↓
Planning Harness
   ↓  (versioned product/solution truth)
PLANNING_READY
   ↓
Adaptive Agent Runtime
```

This seam should be small but hard:

- product evidence/assumptions are explicit;
- product acceptance is observable;
- vertical slice is chosen before implementation decomposition;
- open blocking decisions cannot leak into Runtime as guesses;
- Runtime can issue change requests back when reality invalidates planning.

## 7. Highest-return borrowing order

1. **SPEC planning-readiness schema** — high value, tiny implementation.
2. **Thin Slice + product-level decomposition discipline** — high value, mostly workflow.
3. **Requirement→acceptance/evidence mapping** — high value for reducing false completion.
4. **Planning Handoff contract** — clean Runtime boundary.
5. **Runtime→Planning change request / Repair Loop** — stops silent product drift.
6. **dsh-alpha endpoint/workspace inventory patterns** — valuable for Agent Mesh.
7. **Impact map helper** — only after planning workflow is proven.
8. **Planning UI** — last.

Do not start with a new PM dashboard or fixed planning Agent organization.
