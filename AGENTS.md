# Local Agent Bridge / Computer Runtime — Agent Governance

This file governs the **Local Agent Bridge / Computer Runtime** project only. It does not inherit Controller identity from SelfAlone or any other repository.

## 1. Long-term invariants

These rules are architectural invariants and remain valid after bootstrap identity handling is retired.

### 1.1 Identity layers must remain distinct

Always distinguish the following identities:

1. **Web conversation / execution target ID** — the ChatGPT Web conversation or execution entry currently interacting with the project.
2. **Bridge execution session ID** — the local Bridge session/capability identity used for a concrete local execution channel.
3. **logical Controller ID** — the stable project-level Controller identity owned by orchestration/governance. Exactly one per project.
4. **Adaptive Agent Runtime controller registration** — the machine registry record binding a logical Controller to this repository and its current execution ownership/generation.
5. **Role principal ID** — durable, revocable identity issued **by role** (Controller is the singleton principal; Managers are 0..N principals bound to ledger seats). Not a conversation ID, not a second Controller, not an executor/sub-agent ID.
6. **Assignment lease** — temporary occupancy of a ledger row (and optional child dispatch inside that row). Liveness may use heartbeat. Expiry/revoke returns the seat. Executors and short-lived package owners use leases, not principal IDs.

These values are not interchangeable. A Web execution target ID is **not** a logical Controller ID. A Bridge execution session is **not** proof of Controller ownership. A prompt-provided identifier is **not** machine-verifiable host/session attestation. A Manager principal is **not** Controller authority. An assignment lease is **not** a digital-employee ID.

Never reuse SelfAlone's logical Controller ID for this project.

If a project-specific logical Controller already exists, read it from the real Adaptive Agent Runtime registry. If no project-specific logical Controller exists, do not invent, infer, copy, or synthesize one merely to satisfy a prompt or governance template.

### 1.2 One project-level Controller, many executors

This project has exactly one project-level Controller authority at a time. Desktop Codex, Codex sub-agents, Kimi, Grok, Web executors, independent reviewers, specialists, and other execution backends are **executors / specialists / reviewers**, not additional project Controllers.

Before delegation, inspect the execution and delegation capabilities actually available in the current host/session. Prefer safe parallel dispatch for independent investigation, implementation, and review work when a reliable executor/sub-agent dispatch interface is genuinely available. If such a capability is absent, do not pretend work was delegated and do not block the critical path: continue with the available Local Agent Bridge / Computer Runtime capabilities and record the missing dispatch capability as an execution constraint.

### 1.3 Model and executor routing

backend default provider=grok-build model=grok-4.6 auth_mode=oauth

For independently delegable work, prefer Grok 4.6 through the existing AAR OAuth route when authorized and healthy, to conserve Codex tokens. Codex retains coordination, necessary local execution, and final acceptance. Show the actual AAR start and terminal status cards. The user requires visible external-agent dispatch, running and return cards; Web Goal display is not a user requirement. The user explicitly disabled forced Web Goal display for LAB; preserve that preference and its audit while continuing with the existing ledger and Controller. A blocked health gate or an ambiguous attempt must be reported accurately; never bypass it, silently switch authentication routes, or retry possible side effects.

For LAB Grok caller contracts, explicitly set `AD_EXTERNAL_STREAM_RECORD_MAX_BYTES=1048576` before execution and record that bounded I/O setting with the Assignment budget. The official maximum stays 2 MiB and over-limit records still fail closed. Read large install manifests and gate reports through bounded selected-field summaries; a normal CLI file result may repeat the same source four times. The real 393063-byte record reproduction and bound checks are in `tmp/web-controller-repair-20261023/grok-real-stream-record-boundary-regression.json`. Keep declared and effective total budgets equal and choose them per task; do not silently impose a five-minute caller cap. Preserve NDJSON stdout/stderr with `scripts/grok_delivery_boundary.py` instead of parsing the whole stream as one JSON value. Implementation work must request a canonical delivery receipt; exit zero alone is insufficient. Validate returned artifacts before acceptance.

This project does **not** inherit the old SelfAlone restriction that a Web Controller may use only Web-hosted models.

The current project Controller may select any genuinely available executor according to task fit, including:

- desktop Codex;
- Codex sub-agents;
- Kimi;
- Grok;
- Web models;
- independent reviewers;
- other real executors exposed by the active host/runtime.

Routing must be capability- and task-driven rather than bound to the Controller's UI surface. Do not claim an executor was used unless a real dispatch/receipt exists.

### 1.4 Runtime is development orchestration, not a product dependency

Adaptive Agent Runtime may be used during development for assignment tracking, DAG/READY/WIP projection, concurrency, Reviewer workflows, lifecycle handling, continuation, and project governance.

However, **Local Tool Bridge / Local Agent Bridge and Computer Runtime must remain independently runnable products**. Their runtime path must not require Adaptive Agent Runtime, SelfAlone Controller state, Goal/TASK_LEDGER governance, or any project-management service merely to expose Files, Shell, Process, Browser, or Computer Runtime capabilities.

The supported product architecture must include an independent mode such as:

`ChatGPT Web / other agent -> Local Agent Bridge -> Files / Shell / Process / Browser / Computer Runtime -> local machine`

Adaptive Agent Runtime is allowed to call the Bridge and the Bridge may expose adapters useful to Runtime, but unnecessary hard dependencies are forbidden.


### 1.4.1 Web ChatGPT Executor is an external executor plane

ChatGPT Web model invocation, model routing, and Agent lifecycle must **not** be hard-coded into Local Tool Bridge / Local Agent Bridge or Computer Runtime.

A future **Web ChatGPT Executor** is a separate adapter / executor-plane component. Adaptive Agent Runtime may route work to that executor, and the executor may call Local Tool Bridge to obtain local machine capabilities. The Bridge and Computer Runtime remain model-agnostic capability providers and must not own model selection, prompt lifecycle, Controller state, Goal/TASK_LEDGER state, DAG/READY/WIP, retry policy, or Agent lifecycle.

The intended boundary is:

`Adaptive Agent Runtime -> Web ChatGPT Executor -> Personal ChatGPT Web`

and, when local capabilities are needed:

`Web ChatGPT Executor -> Local Tool Bridge -> Files / Shell / Process / Browser / Computer Runtime -> local machine`

Current interfaces must preserve this separation even during PoC shortcuts. Correlation between Web conversation/execution identity and Bridge execution sessions belongs in the external adapter/orchestration layer unless the Bridge is merely reporting machine-verifiable transport/session facts.

### 1.4.2 Formal Web ChatGPT Executor contract

The Web ChatGPT Executor is a provider-specific executor adapter behind the existing Adaptive Agent Runtime canonical Assignment / route / lifecycle machinery. Runtime remains responsible for Controller, Goal, DAG, READY/WIP, canonical Assignment, route selection, timeout/retry/cancel/recovery policy, terminal handling, Reviewer acceptance, ownership/generation fencing, and SAME-Controller continuation. Runtime must not contain ChatGPT DOM selectors, page-button logic, Playwright/CDP selectors, or browser implementation details.

The Web ChatGPT Executor Adapter owns only the provider execution plane: authenticated Personal ChatGPT Web session reuse, real conversation creation/binding, unique adapter execution_id creation, assignment/execution/browser-session/conversation correlation, requested-versus-observed model handling, prompt dispatch, queued/running/terminal observation, automatic completion detection, result extraction, bounded timeout/cancel/recovery, and emission of machine-readable execution events.

Browser automation is only an implementation mechanism. Opening ChatGPT, typing a prompt, scraping text, or manually creating a chat is not a formal Executor spawn. A formal execution requires a real dispatch primitive plus a unique execution_id and durable correlation from assignment -> execution -> conversation -> result -> terminal.

Identity layers must remain distinct: Runtime logical Controller identity, Runtime assignment identity, Web Executor execution identity, browser session identity, ChatGPT conversation identity, provider/model identity, and Local Tool Bridge execution-session identity. If OpenAI does not expose machine-verifiable host/conversation attestation, the adapter may provide machine-verifiable adapter execution identity and persisted correlation, but must label OpenAI host attestation as unavailable/partial rather than fabricate it.

### 1.4.3 Host-neutral pluggable executor routing

Controller host type, Executor type, model/provider type, and Local Tool Plane are orthogonal. A Web Controller may route to Web ChatGPT, desktop Codex, Kimi, Grok, another external executor, or an independent Reviewer when the canonical route and real executor capability allow it. Web ChatGPT Executor may optionally call Local Tool Bridge for local capabilities. Local Tool Bridge and Computer Runtime remain independently runnable when Adaptive Agent Runtime is off.

Adaptive Agent Runtime should evolve through its existing Executor/Assignment abstraction toward pluggable provider adapters such as web_chatgpt, web_claude, web_gemini, codex_desktop, kimi, grok, or local_llm. Provider-specific browser/session logic stays inside the adapter; adding a new provider must not modify Controller governance or Local Tool Bridge.

SelfAlone is a separate project. It may be used only as an external E2E target / fixture when that is useful for validating the Bridge or Computer Runtime. This project must not modify SelfAlone main, reuse its task ledger or Controller identity, or take over its Controller lifecycle as part of normal development.

### 1.5 Controller-gated actions must follow machine facts when available

Whenever machine-verifiable registration, ownership, target generation, session attestation, or capability verification exists, those machine facts override prompt claims and historical assumptions. Ambiguous ownership must fail closed for Controller-only actions rather than silently forking or impersonating a Controller.

### 1.5.1 Cross-project repair ownership and no-duplicate-work rule

The LAB Controller owns diagnosis, dependency tracking, acceptance criteria, routing, review, integration, and release decisions for LAB. It must **not** implement a competing fix inside Adaptive Agent Runtime or another external infrastructure repository when a dedicated repair session/worktree already owns that defect.

When LAB is blocked by an external Runtime/Host-infrastructure defect:

1. fresh-read the external defect owner, worktree/branch, candidate and terminal state;
2. record the blocker and exact acceptance criteria in LAB governance facts;
3. send/route evidence to the existing repair owner instead of opening a competing implementation;
4. continue any independent LAB READY work that does not violate the blocker;
5. after the repair owner produces an immutable candidate, LAB may independently review, integrate/accept, install, and run LAB-specific Live-E2E gates.

LAB may take over implementation only when the user explicitly reassigns it, or machine facts prove that the prior repair owner is terminal/abandoned and the Runtime performs a canonical reassignment. A critical path blocker alone is **not** permission for the Controller to steal the repair task.

This rule does not prevent the Controller from reading external source or reproducing a failure when needed to produce precise evidence; it prevents duplicate implementation ownership.

### 1.6 Safety-check-friendly execution discipline

When using Local Agent Bridge or another high-privilege local execution surface, prefer **natural engineering steps with clear, bounded side effects** instead of packing unrelated high-impact actions into one opaque tool call.

In practice, keep normal development phases distinct when feasible, for example:

- inspect/read current state;
- write or patch the intended file(s);
- run tests or verification;
- run broader E2E or privileged operations only after the preceding step is clear.

This is an execution-quality and diagnosability rule, **not a method for bypassing platform safety controls**. File size or line count alone is not considered the trigger for a safety block; a large write may be valid, while a small action may still be blocked. The relevant engineering concern is whether one request combines multiple sensitive or high-impact behaviors in a way that makes intent and side effects difficult to evaluate.

If a platform safety layer explicitly blocks an operation:

1. preserve the original block/error as evidence;
2. determine whether the blocked request unnecessarily bundled independent development steps;
3. if so, resume using the project's ordinary stepwise workflow, with each step independently justified;
4. do **not** encode, obfuscate, fragment, rename, shell-wrap, route through another executor, or use GUI automation for the purpose of evading the safety decision;
5. if the same underlying action remains blocked when expressed as a normal standalone engineering step, treat it as a real platform constraint and stop that action rather than attempting further bypasses.

Agents must not infer that `smaller command = allowed` or `larger command = blocked`. The goal is clear intent, least necessary side effect, easier review, and reliable failure localization.

## 2. Current Controller identity

Unique logical Controller: `a697051d-402b-4062-a883-a29ba8b395e9`. The machine registry is authoritative; never create a second Controller to repair or resume this project.

**Current owner, verified 2026-10-24:**

- host `web`;
- session `6ab93a76-4e6c-83e9-95ae-71d28091ef08`;
- Web target generation **14**, execution ownership generation **18**;
- current installed AAR revision `92c1353ca1e634ec0e63815c5f86304a8daaac00` (user-authorized local maintenance; exact revision subsequently published to GitHub); same-Web14/18 exact ACK and official Live-E2E accepted at 2026-10-24 13:00:29 CST. Preserve this acceptance and the original f4 accepted evidence.

Desktop Codex session `01a16273-7fed-7f92-8abc-ab0737b4f3da` continues the user-authorized Mac execution and integration work. It is an executor, not a second Controller and not an implicit ownership transfer. Fresh-read registry generations before protected operations. Older desktop/Web target identities are audit history only; opening or continuing a desktop chat must not reclaim ownership.

**Enforcement:** use existing canonical Host attestation, registry/ownership fences and AAR gates. Do not fabricate identity receipts, replay old wakes, or replace the current entry to satisfy prose. Current evidence and remaining work are in the task ledger and dated handoff document.

## 3. Remaining identity gate (LAB-GOV-02)

Binding is implemented. What remains is Live-E2E **acceptance freeze**, not inventing attestation:

`host-attested current target → machine CONFIRMED SAME-Controller wake → clean CLOSED controller cycle → accept-live-e2e`

Until `accept-live-e2e` is written, `live_e2e_required` stays true. Do not treat missing acceptance as missing binding. Do not replay old wakes, forge current-entry, or relabel `RESULT_UNKNOWN` as success.

## 4. Adaptive Agent Runtime on development sessions

Use the name **Adaptive Agent Runtime (AAR)**. `adaptive-delivery` is a leftover directory/registry filename, not the system name.

AAR governs **development/orchestration** sessions on this repo (Controller, Codex tasks, future Manager seats). Ordinary product use of LAB Connect (for example ChatGPT calling Feishu) does **not** require AAR.

Intensity by complexity, one ledger:

- **Quick**: Q&A or single-file low risk. Still AAR: no false completion, no scope creep, no second ledger.
- **Standard**: writes to main-tree code/docs. Evidence, file ownership, one writer per file. Work must map to a ledger row (Controller attaches on dispatch; user need not remember IDs).
- **Strict**: Controller control plane — project-wide runnable recompute, dispatch, acceptance, integration, Stop receipts.

Rules:

- One `TASK_LEDGER.md`. Executors do not CAS it. Controller updates it from evidence.
- Duplicate/conflict checks happen at **dispatch = that ledger write**, not after the fact and not by each manager scanning a stale copy.
- Appointing a new host/session as the current Controller entry (`replace-desktop-session` / equivalent replace of the current target) claims execution ownership in the **same lock**. `bind-*-session` only adds lineage and does not take duty. Opening a window is not an appointment.

- `BLOCKED` freezes that row only. Unfinished parent/child rows require recompute and dispatch. Do not idle because one Goal or one row is blocked.
- Session Goal is allowed for the assigned package. Only the Controller rolls the **project** milestone Goal.
- Human confirmation is only: payment, product-direction change, authorization gates, data security, and hard-to-reverse high risk (including public push/release). Local main commits, ordinary engineering, and authorized disposable tenant writes do not wait on the user.
- Do not use the user as an acceptance clicker. Controller performs H3/H4 at the real entry.
- Executors / sub-agents: assignment lease, no principal ID. Temporary package owners: the same lease family, optional in-package spawn, no principal ID even if the task is long or crosses windows. **Standing Manager seats only** get a principal ID; issue on demand, revoke when the seat is idle.
- Manager principals reuse the same issuance/binding model as Controller (role principal + session bind + revoke). They do not copy Controller gates (ACK / accept-live-e2e / whole-ledger CAS / project Goal rollover). Web attestation strength follows Host facts; a principal ID is not a login session.

## 5. Development continuity

Governance-document edits are not a reason to stop project execution. After changing this file, return to the current critical-path PoC, preserve unrelated project work, and continue from fresh repository/runtime facts.
