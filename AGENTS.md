# Local Agent Bridge / Computer Runtime — Agent Governance

This file governs the **Local Agent Bridge / Computer Runtime** project only. It does not inherit Controller identity from SelfAlone or any other repository.

## 1. Long-term invariants

These rules are architectural invariants and remain valid after bootstrap identity handling is retired.

### 1.1 Identity layers must remain distinct

Always distinguish the following identities:

1. **Web conversation / execution target ID** — the ChatGPT Web conversation or execution entry currently interacting with the project.
2. **Bridge execution session ID** — the local Bridge session/capability identity used for a concrete local execution channel.
3. **logical Controller ID** — the stable project-level Controller identity owned by orchestration/governance.
4. **Adaptive Agent Runtime controller registration** — the machine registry record binding a logical Controller to this repository and its current execution ownership/generation.

These values are not interchangeable. A Web execution target ID is **not** a logical Controller ID. A Bridge execution session is **not** proof of Controller ownership. A prompt-provided identifier is **not** machine-verifiable host/session attestation.

Never reuse SelfAlone's logical Controller ID for this project.

If a project-specific logical Controller already exists, read it from the real Adaptive Agent Runtime registry. If no project-specific logical Controller exists, do not invent, infer, copy, or synthesize one merely to satisfy a prompt or governance template.

### 1.2 One project-level Controller, many executors

This project has exactly one project-level Controller authority at a time. Desktop Codex, Codex sub-agents, Kimi, Grok, Web executors, independent reviewers, specialists, and other execution backends are **executors / specialists / reviewers**, not additional project Controllers.

Before delegation, inspect the execution and delegation capabilities actually available in the current host/session. Prefer safe parallel dispatch for independent investigation, implementation, and review work when a reliable executor/sub-agent dispatch interface is genuinely available. If such a capability is absent, do not pretend work was delegated and do not block the critical path: continue with the available Local Agent Bridge / Computer Runtime capabilities and record the missing dispatch capability as an execution constraint.

### 1.3 Model and executor routing

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

SelfAlone is a separate project. It may be used only as an external E2E target / fixture when that is useful for validating the Bridge or Computer Runtime. This project must not modify SelfAlone main, reuse its task ledger or Controller identity, or take over its Controller lifecycle as part of normal development.

### 1.5 Controller-gated actions must follow machine facts when available

Whenever machine-verifiable registration, ownership, target generation, session attestation, or capability verification exists, those machine facts override prompt claims and historical assumptions. Ambiguous ownership must fail closed for Controller-only actions rather than silently forking or impersonating a Controller.

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

## 2. Bootstrap / Temporary Controller Binding

> **TEMPORARY / BOOTSTRAP ONLY — NOT FINAL ARCHITECTURE**
>
> This section exists only until the new Local Agent Bridge can provide sufficiently reliable execution-session authentication and host/session attestation. Future agents must not reinterpret this bootstrap state as the permanent Controller identity model.

### 2.1 Current manual authorization

The user has explicitly authorized the following current Web execution target to act as the **only project-level Controller execution entry during bootstrap development**:

- `web_execution_target_id = 6a9e4037-0a00-83ea-bb8e-1301c5f6b979`
- `provenance = manual_user_authorized`
- `binding = temporary`
- `host_attested = false`

This authorization permits the Web session to perform project-level planning, dispatch, acceptance, review orchestration, and development governance during the bootstrap phase.

It does **not** mean the host has cryptographically or machine-verifiably authenticated that conversation. The presence of the ID in a user prompt or in this file must never be described as `host_attested=true`, machine-verifiable identity binding, or proof that the caller currently operating the Bridge is that Web conversation.

### 2.2 Current Runtime registration fact

The canonical Adaptive Agent Runtime registry now contains one project-specific logical Controller registration for this repository:

- `project_identity = /Users/echoman/Documents/ChatGPT/Local-Agent-Bridge/.git` (Git common-dir; this is the Runtime project identity boundary)
- `logical_controller_id = a697051d-402b-4062-a883-a29ba8b395e9`
- `web_execution_target_id = 6a9e4037-0a00-83ea-bb8e-1301c5f6b979`
- `provenance = manual_user_authorized`
- `binding = temporary`
- `host_attested = false`
- Runtime registry/session projection currently reports `BOUND_REGISTERED_WEB_SESSION` for the Web alias. This means the registry association is present; it **does not** upgrade the binding to host-attested origin identity.

Therefore:

- the logical Controller ID above is the only logical Controller for this project;
- no SelfAlone Controller ID may be reused;
- the Web execution target remains a separate execution/session identity and must never be promoted into the logical Controller ID;
- `manual_user_authorized` remains bootstrap-only authorization until the Local Agent Bridge supplies machine-verifiable host/session attestation;
- Runtime machine facts override this file if ownership/generation later changes through a supported canonical operation.

### 2.3 Bootstrap governance behavior

During this temporary phase:

- the authorized Web execution target above is the single project-level Controller execution entry;
- all desktop Codex/Codex sub-agent/Kimi/Grok/Web/reviewer workers remain non-Controller executors;
- the Controller may choose executors by task type and real availability;
- no model family is privileged merely because the Controller is running in Web;
- the Controller must inspect actual dispatch/execution capabilities before claiming delegation;
- development may use Adaptive Agent Runtime as orchestration infrastructure, while the Bridge/Computer Runtime product remains independently runnable.

If the active Web conversation changes before strong binding exists, the new execution target must receive fresh explicit user authorization; agents must not silently inherit this Web ID as current ownership.

## 3. Automatic retirement conditions for bootstrap binding

The Bootstrap / Temporary Controller Binding must be retired once the new Local Agent Bridge can provide sufficiently reliable machine facts for all of the following:

- execution-session authentication;
- host/session attestation;
- challenge/capability verification;
- execution-target identity.

And Adaptive Agent Runtime can consume those machine facts to verify the chain:

`verified execution target -> unique logical Controller -> generation / ownership validation`

Once that chain is operational and verified, `manual_user_authorized` must stop being accepted as the normal basis for Controller identity.

The migration must then:

1. establish this project's formal unique logical Controller ID through the supported Runtime mechanism;
2. bind the current Web execution target using the new machine-verifiable identity/attestation mechanism;
3. gate Controller actions on the verified binding, current ownership, and generation;
4. update this `AGENTS.md` to describe the permanent mechanism;
5. delete or archive Bootstrap manual-binding runtime configuration;
6. stop requiring the user to provide a Web conversation ID in a prompt as identity proof.

Historical bootstrap records may be retained only as audit history and must not remain an active authorization source.

## 4. Product acceptance gate: eliminate manual Web Controller binding

The ability to retire the current manual Web Controller binding is itself a required acceptance criterion for the Local Agent Bridge PoC and final design.

A successful final identity E2E must demonstrate with machine-verifiable evidence:

`ChatGPT Web -> new Local Agent Bridge identity/attestation -> Adaptive Agent Runtime -> verified unique logical Controller`

Passing this gate requires more than a prompt assertion, copied conversation ID, secret URL, process existence, browser DOM observation, or manually trusted metadata. The evidence must show what identity the Bridge verified, how the execution target was attested, which unique logical Controller Runtime resolved, and which current generation/ownership check authorized the Controller action.

Until this gate passes, Controller identity remains bootstrap/degraded and must be labeled accordingly.

## 5. Development continuity

Governance-document edits are not a reason to stop project execution. After changing this file, return to the current critical-path PoC, preserve unrelated project work, and continue from fresh repository/runtime facts.
