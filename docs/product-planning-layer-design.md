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

## 15. 2026-09-17 user-approved migration and own Computer Engine roadmap

This addendum records explicit user decisions from the current conversation. It does not retroactively promote the whole research candidate, bind this conversation as Controller, or close any Runtime task. Canonical product and technical counterparts are the dated sections of `SPEC.md` and `TECHNICAL.md`.

### Confirmed outcomes

LAB is the default local execution path; AI-Bridge remains installed as explicit emergency fallback, never silent automatic failover. Prioritize current Controller identity verification/binding and current-account LAB onboarding. After LAB migration, evolve the existing Computer Runtime into an independently runnable replacement for external Computer Helper.

The selected interaction strategy is Accessibility/structured UI first, visual grounding when structured UI is inadequate, then bounded coordinate actions and fresh outcome verification. An external multimodal Agent may provide vision; an embedded OCR/grounding model is not a first-release prerequisite. Model/provider selection must stay outside the local capability core.

### Delivery slices and evidence

| Slice | Deliverable | Acceptance evidence |
| --- | --- | --- |
| Identity priority | Reuse the unique project Controller; verify the current entry and rotate ownership only through supported mechanisms | Fresh Host evidence, exact current generation, canonical handoff; separate wake/CLOSED/accept-live-e2e gates |
| LAB Connect | The logged-in account and conversation invoke the canonical production LAB tools | Account Connector, current-turn tool exposure, real read/write/edit/command/job success/cancel/timeout, execution-path provenance |
| Native hands/eyes | Existing Computer Runtime operates a harmless native fixture | User-granted permissions, real observe/find/click/type/key/scroll, stale-target denial, independent review |
| Hybrid interaction | Canvas/custom-drawn fixture can be operated without AX controls | Snapshot-bound bbox/coordinate action, scale/display/window binding, pre-action freshness checks, post-action observation |
| Dependency retirement | Ordinary local execution no longer depends on AI-Bridge or external Helper | Controlled OFF tests after the replacement works; recovery remains explicit and auditable |

### Safety and recovery invariants

Account login is not Controller attestation. Emails, titles, URLs, foreground tabs and model statements cannot establish current execution ownership. Do not create a second Controller or hand-edit canonical receipts. Unknown post-dispatch outcomes are not retry permission.

A visual target must carry session/generation, observation identity, window/PID, image hash, timestamp/TTL, image/window geometry, display/scale/coordinate transformation and bounding box. Confidence is advisory. Revalidate target/focus/geometry before action and re-observe after action. Frame drift, occlusion, stale observation, cross-session access, user input and uncertain outcomes require safe interruption rather than blind retries.

System authorization remains a user operation. Vision is a compatibility fallback, not a permission bypass. Screenshots, logs and model-visible observations minimize sensitive content; login/security/permission targets are excluded by default.

### Parallelism and product boundaries

Identity/production changes stay on the critical path, but independent documentation, offline regression, Computer fixtures and isolated implementation can progress without Controller privileges. Actual executable ownership stays in Runtime; this roadmap is not another task ledger. Real agents are only described as dispatched when a genuine dispatch ID/receipt exists. Existing external repair owners are not duplicated.

Ordinary LAB Connect use bypasses Planning/Runtime. The protected Controller profile may require a Runtime hook; its readiness must not be confused with the independent Tool Plane's readiness. Do not disable the last usable fallback connection before a real LAB connection has passed.

### Success measurement

Compare old and new paths on the same fixture/environment: completion rate, incorrect-target actions, safe refusals, median/tail latency, timeout rate, reconnect/restart recovery, credential leakage and dependency-OFF behavior. Architecture alone does not justify claiming LAB is already more stable.

## 16. 2026-09-19 stack contract: AAR governance vs LAB tool plane

This section freezes the user-confirmed root-cause scheme. It does not turn Adaptive Agent Runtime into a LAB product feature, and it does not put Feishu/Computer stories into AAR.

### Three planes

- **Adaptive Agent Runtime (AAR)** — governance runtime: unique Controller, one ledger per project, dispatch, identity, duty switch, Stop/Yield. User-level, across projects and AI hosts. Lives in the AAR skill, `install_skill`, host hooks, and the user-level controller registry.
- **Local Agent Bridge (LAB)** — local capability plane: Connector/tunnel → Bridge → files/shell/Feishu/Computer. Any client may call it. LAB must run without AAR being online.
- **Business repositories** — projects. They use AAR to manage work and LAB to touch the machine. They do not own either plane.

Feishu and Computer belong in LAB SPEC/crates. Governance rules belong in AAR. A project `AGENTS.md` may record that project's Controller ID and that the product does not depend on AAR; it must not be the AAR rulebook.

### Global AAR

One installed skill revision (manifest hashes must match). Each AI host attaches the same lifecycle hooks (Codex already does). New tools get a host adapter, not a second AAR. Ledgers stay per-project; the engine is global.

### Force Controller project management (hooks, not prose)

Applies to the **Controller** session of a project:

1. SessionStart injects that project's ledger working set (`READY/ACTIVE/VERIFY/BLOCKED`).
2. Stop/Yield is refused while this control event still has `READY`/`ACTIVE` work, unwritten ledger updates, or Continuation Debt. `BLOCKED` rows waiting on an external condition are not a reason to keep the turn open, and they are not a reason to treat the project as empty.
3. Main-tree writes and local merge require a ledger row and file ownership.
4. Controller spawn into the project main tree requires a ledger row and ownership.
5. Recompute must fresh-read; stale `BLOCKED` labels are invalid.

Ordinary sessions may spawn ephemeral sub-agents. Writing that project's main tree requires a ledger row or an assignment lease. Temporary owners use leases (same family as sub-agent lease/heartbeat). Standing Manager seats only get a principal ID. Executors never get one.

Controller duties only: ledger, recompute, dispatch, accept, merge main, roll the **project** Goal. Controllers do not implement. Session Goals may cover one assigned package. Human confirmation: payment, product-direction change, authorization/data-security, hard-to-reverse risk. Local merge, ordinary install, disposable tenant writes do not wait on the user.

### Identity and duty

One logical Controller ID per project. Web and desktop are entries, not two Controllers. Appoint / `replace-*-session` of the current entry claims execution ownership in the same lock. `bind-*-session` is lineage only. Opening a window is not an appointment.

Machine gates cover: wrong Controller, ledger CAS races, appoint without duty switch, dirty AAR install, Controller stopping with runnable work, Controller spawning project work with no row. They do not cover model chatter. Fix hooks; do not add project essays.

