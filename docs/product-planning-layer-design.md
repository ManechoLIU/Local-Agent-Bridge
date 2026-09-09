# LAB Product Planning Layer — Research Candidate

> Checked: 2026-09-09
> Status: research candidate only; not authoritative until accepted by the unique LAB Controller.
> Goal: close the current gap between vague product intent and execution-ready Runtime work without turning LAB into a heavyweight PM suite.

## 1. Decision

**Development-before planning should be a separate logical layer from Adaptive Agent Runtime.**

Do not build it as a separate daemon, database, or large product in the first version.

Use a lightweight **Planning Harness** inside LAB Projects:

```text
Raw idea / PRD / user feedback / research
          ↓
Product Planning Layer
  Discovery → clarify → scope → product solution → delivery slices
          ↓
PLANNING_READY contract
          ↓
Adaptive Agent Runtime
  DAG / READY / WIP / Agent ownership / A2A / review / recovery / acceptance
          ↓
LAB Tool Plane / Computer Runtime
```

The Planning Layer decides **what should be built and what a good result means**.
The Runtime decides **who executes which confirmed work, in what order, and how execution stays recoverable and verifiable**.

## 2. Why separate the layers

The current Runtime is strong at:
- project execution governance;
- current Goal / task ledger;
- READY/WIP;
- Agent delegation;
- Reviewer/acceptance;
- recovery and continuation;
- side-effect correctness;
- provider/host execution.

It is weaker at:
- converting a fuzzy user idea into a validated product problem;
- distinguishing user evidence from assumptions;
- identifying product scope/non-goals before implementation;
- selecting the smallest valuable vertical slice;
- producing an implementation-ready product/solution baseline;
- decomposing by user value rather than by code/module;
- identifying missing product decisions before Runtime starts work.

If Runtime is forced to solve those questions during execution, it becomes both product strategist and delivery engine, and task decomposition inherits unverified assumptions.

## 3. Two kinds of decomposition

This boundary is critical.

### Product-level decomposition — Planning Layer

Break a goal into:
- user outcomes;
- product capabilities;
- vertical Thin Slices;
- milestones;
- acceptance criteria;
- dependencies;
- non-goals;
- rollout/validation order.

Example:

```text
"Support multiple Web AI providers"
  → Provider-neutral identity contract
  → one official connector reference path
  → one cross-provider delegation path
  → capacity-aware routing
  → productized onboarding
```

This is not yet Agent/file ownership.

### Execution-level decomposition — Runtime

Break a confirmed milestone into:
- work packages;
- Agent ownership;
- file/worktree boundaries;
- READY/WIP;
- parallel waves;
- Reviewer assignments;
- verification commands;
- recovery checkpoints.

Example:

```text
"Implement provider-neutral identity contract"
  → writer package A: schema
  → writer package B: adapter seam
  → reviewer package
  → integration package
```

Planning Layer must not micromanage these Runtime-level details.

## 4. Minimal permanent artifacts

Do **not** introduce another parallel product-plan truth source by default.

Reuse the project documents LAB already owns.

### SPEC.md — planning authority

Extend/standardize it as the product source of truth:

- problem / opportunity;
- target user and real user evidence;
- current state;
- desired outcome;
- scope;
- non-goals;
- product behavior / user journeys;
- success criteria;
- acceptance criteria;
- assumptions vs confirmed facts;
- constraints;
- open decisions;
- product risks;
- version/change history.

### DESIGN.md — only when product experience needs it

Own:
- user flows;
- interaction;
- visual behavior;
- product states;
- approved references;
- UX acceptance.

Do not require it for backend/infrastructure-only work.

### TECHNICAL.md — solution baseline after product intent is stable enough

Own:
- architecture;
- integration surfaces;
- contracts;
- data;
- security;
- failure/recovery;
- rollout;
- Harness/evidence strategy;
- technical constraints.

### TASK_LEDGER.md — Runtime-owned, not Planning-owned

Create/populate executable work only after the planning boundary passes.

Planning may propose milestones and slices, but Runtime materializes current executable work into the canonical task ledger.

This keeps:
- product truth in SPEC/DESIGN/TECHNICAL;
- execution truth in TASK_LEDGER;
- runtime machine state outside product documents.

## 5. Planning readiness gate

Before a nontrivial product initiative enters Runtime, it should answer a small set of questions.

### Required

1. **Problem** — what real problem or opportunity is being addressed?
2. **User** — who experiences it?
3. **Evidence** — what is observed vs assumed?
4. **Outcome** — what observable change means success?
5. **Scope** — what is in?
6. **Non-goals** — what is explicitly out?
7. **Thin Slice** — what is the smallest valuable end-to-end delivery?
8. **Acceptance** — how will we know the slice is correct?
9. **Constraints** — technical/business/security/time constraints.
10. **Open decisions** — which unresolved choices truly block implementation?

### Optional by need

- UI/visual baseline;
- code/system impact map;
- rollout;
- business metrics;
- production authorization;
- commercial assumptions;
- migration plan.

### Result

```text
PLANNING_READY
PLANNING_READY_WITH_OPEN_NONBLOCKERS
INPUT_REQUIRED
DISCOVERY_REQUIRED
```

Runtime should not silently invent a blocking product decision.

## 6. Three planning depths — reuse existing risk trimming

Do not create another complex governance taxonomy.

Reuse the same philosophy as Adaptive Delivery.

### Quick

Use when:
- requested behavior is already clear;
- low product ambiguity;
- small/local change.

Action:
- confirm desired behavior and acceptance;
- go directly to Runtime or direct execution.

No product workshop.

### Standard

Use for:
- normal product feature;
- multiple user states;
- cross-component impact;
- moderate ambiguity.

Action:
- clarify problem/user/outcome;
- update SPEC;
- define one or more vertical slices;
- create solution baseline as needed;
- run a focused planning review;
- hand off to Runtime.

### Strict / Discovery

Use for:
- new product;
- unclear user/problem;
- broad platform change;
- high-cost/high-risk commitment;
- many stakeholders or conflicting evidence.

Action:
- evidence collection;
- current workflow mapping;
- assumptions and unknowns;
- competing solution options;
- Thin Slice selection;
- product review;
- only then PLANNING_READY.

Do not make strict planning mandatory merely because implementation is technically complex.

## 7. Planning workflow

Recommended minimal flow:

```text
Intake
  ↓
Evidence & Problem Clarification
  ↓
Product Contract
  ↓
Solution Options / Impact
  ↓
Thin Slice + Milestones
  ↓
Planning Review
  ↓
PLANNING_READY
  ↓
Runtime
```

### Intake

Accept:
- rough idea;
- PRD;
- user feedback;
- competitor research;
- screenshots;
- existing code/product behavior.

Do not require the user to pre-format inputs.

### Evidence & Problem Clarification

Classify important claims as:
- confirmed fact;
- user statement;
- interpretation/hypothesis;
- unknown.

Ask only questions that materially affect scope, value, acceptance, or irreversible architecture.

### Product Contract

Update SPEC with:
- goals/non-goals;
- behavior;
- user states;
- acceptance;
- constraints.

### Solution Options / Impact

Only if meaningful:
- compare candidate approaches;
- identify systems/modules affected;
- identify shared contracts;
- flag irreversible choices;
- decide which choices can be deferred.

### Thin Slice + Milestones

Prefer vertical outcome slices over layer/module decomposition.

Bad:
- frontend;
- backend;
- database;
- tests.

Better:
- user can connect provider;
- provider can invoke one controlled LAB tool;
- execution is observable;
- failure is recoverable.

Each slice should have an acceptance outcome.

### Planning Review

Independent review is useful for:
- new product/platform direction;
- ambiguous scope;
- major architecture;
- safety/commercial boundaries.

Reviewer checks:
- did we solve the stated problem?
- is evidence being mistaken for assumption?
- is scope bloated?
- is the Thin Slice actually end-to-end?
- are acceptance conditions observable?
- are there hidden product decisions being deferred into development?

## 8. Handoff to Runtime

Planning does not directly populate Agent assignments.

It produces a normalized handoff:

```text
initiative_id
product_spec_version
technical_baseline_version?
goal
non_goals
milestones[]
vertical_slices[]
acceptance[]
dependencies[]
known_risks[]
open_nonblocking_decisions[]
blocking_decisions=[]
evidence_refs[]
recommended_governance_depth
```

Runtime then:
1. fresh-reads current repository/project facts;
2. reconciles the planning handoff with actual implementation state;
3. derives executable DAG/READY;
4. chooses Agents/Providers;
5. assigns ownership;
6. validates and integrates.

A Planning Handoff is an input contract, not a frozen task graph.

## 9. Feedback path from Runtime back to Planning

Borrow the Repair Loop idea.

Runtime discoveries that change **implementation facts** stay in Runtime/TECHNICAL.

Runtime discoveries that invalidate **product assumptions** create a planning change request:

```text
planning.change_request
  reason
  evidence
  affected_requirement
  impact
  proposed_options
```

Examples:
- user flow is technically impossible under provider terms;
- acceptance condition conflicts with actual platform behavior;
- requested capability requires a permission user does not have;
- two requirements contradict each other.

Agents must not silently rewrite product intent to make implementation easier.

## 10. Agent roles: dynamic, not fixed bureaucracy

Do not copy a permanent four-Agent or thirteen-stage workflow.

Possible planning capabilities:
- Research / Evidence Agent;
- Requirements Agent;
- Product/Solution Agent;
- Technical Impact Agent;
- Planning Reviewer.

Runtime chooses 0..N based on need.

For a simple change, one Agent can do the whole planning pass.
For a new platform initiative, Research + Product + Technical + Reviewer may be useful.

## 11. Relationship to A2A Agent Mesh

Planning Agents can use the same Agent Mesh.

Example:

```text
Product Agent
  ↔ User Research Agent
  ↔ Technical Architect Agent
  ↔ Commercial/Policy Agent
```

They may negotiate directly.

Canonical product decisions are still written to SPEC/DESIGN/TECHNICAL only after the appropriate decision/approval path.

No new planning-specific message bus.

## 12. What Planning Layer should NOT become

Avoid:
- Jira clone;
- generic roadmap SaaS;
- separate product database;
- fixed PM bureaucracy;
- mandatory PRD for every change;
- fixed Agent count;
- 13 mandatory stages copied from another team;
- full ontology/data modeling for every feature;
- automatic market claims from weak evidence;
- a second task ledger;
- a second Controller.

The first version is primarily:
**skills/workflows + artifact schema + readiness gate + handoff contract**.

## 13. Whole LAB product stack after this change

```text
LAB Projects
│
├─ Product Planning Layer
│    idea/PRD → evidence → SPEC/DESIGN/TECHNICAL → PLANNING_READY
│
├─ Adaptive Agent Runtime
│    DAG / READY / WIP / governance / recovery / acceptance
│
├─ A2A Agent Mesh
│    cross-host / cross-provider direct collaboration
│
├─ Provider Runtime Adapters
│    reuse native Codex/Kimi/Qwen/etc abilities where available
│
├─ LAB Tool Plane
│    Files / Shell / Process / Jobs / Browser
│
└─ Computer Runtime
     controlled desktop execution
```

LAB Connect may bypass Product Planning and Runtime entirely for ordinary tool use.

## 14. Minimal implementation plan

Do not interrupt current ChatGPT reference/M0 work.

### P0 — freeze contract only

- define SPEC planning fields;
- define planning readiness result;
- define Planning Handoff;
- define planning.change_request.

No new service.

### P1 — planning skill/workflow

After core reference stability:
- implement Intake/Clarify;
- generate/update SPEC;
- Thin Slice/Milestone planning;
- optional planning review;
- handoff into existing Runtime task derivation.

### P2 — evidence/impact helpers

Only if measured useful:
- code impact map;
- source/evidence index;
- requirement→acceptance mapping;
- plan diff/change request.

### P3 — product UI

Only after real usage proves value:
- Planning view;
- open decisions;
- evidence/assumption visualization;
- milestone readiness.

Do not build UI before the workflow proves itself.
