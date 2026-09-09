# LAB Robustness Migration — Source Audit & Legacy Failure Matrix

> Scope: selective hardening only. Preserve Local Tool Bridge / Computer Runtime / Web Executor / Adaptive Agent Runtime separation. Do not delay final Host SAME-Controller Live-E2E.

## Source audit decisions

### XiaoDuoYa/codex-with-chatgpt

Decision: selectively absorb state machines, diagnostics, auth lifecycle and tests; do not fork wholesale and do not make Codex a product runtime dependency.

High-value ideas:
- layered doctor and bounded self-repair;
- workspace-scoped bridge, tunnel and Connector lifecycle;
- explicit repair-needed state instead of silent stale endpoint reuse;
- Quick tunnel versus stable named-hostname semantics behind a tunnel-provider abstraction;
- OAuth 2.1 + PKCE, client registration, short-lived access token, rotating refresh token and revocation;
- one-time pairing code with TTL/rate limits;
- workspace-bound auth/session state;
- loopback-only local bind and private state;
- secret-redacting logs.

LAB is already stronger in signed per-request capability proof, generation-fenced capability/session state, 0600 UDS ingress, hardlink/symlink workspace protection, backend authentication, crash-safe master-key rotation and machine-attested Web Controller identity. Those guarantees must not be weakened.

### anightmonarch/codex-bridge-chatgpt

Decision: absorb correlation, distrust-boundary, structured result, receipt and completion-gate ideas into WEX-02/03; do not inherit a Codex-required architecture.

High-value ideas:
- bounded/sanitized context packet;
- packet/result correlation;
- Web model output remains untrusted until local acceptance;
- browser evidence/hashes are correlation and audit evidence, not remote-model attestation;
- deterministic completion gate;
- explicit accepted/rejected/deferred result state;
- one-send / uncertainty-stop discipline;
- page existence is not terminal evidence.

LAB mapping:

assignment_id -> execution_id -> browser_session_id -> conversation_id -> result -> terminal_receipt -> Runtime acceptance -> SAME Controller continuation

## AI_BRIDGE_LEGACY_FAILURE_MATRIX

| # | Historical symptom | LAB current status | High-value source idea | Decision | Priority | Required acceptance |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Tunnel/endpoint drift, stale Connector, VPN/restart changes address | PARTIAL+: canonical tunnel process/profile metadata + endpoint generation v0 implemented; Connector binding still missing | doctor + Quick/Named repair states | Keep v0; extend Connector/hostname lifecycle | P1 | endpoint generation; stale detection; no dual-active endpoint; repair; Wi-Fi/VPN/sleep fault injection |
| 2 | One failed action makes whole Bridge look dead | P0 IMPLEMENTED: layered read-only doctor separates backend/bridge/tunnel/MCP/auth/tool registry and leaves future layers explicit | layered doctor | Keep and extend probes | P0/P1 | per-layer HEALTHY/DEGRADED/BROKEN; unrelated healthy layers stay healthy |
| 3 | Real shell success but adapter reports fake exit code | MOSTLY IMPLEMENTED in hardened CatDesk | regression discipline | Keep implementation; add formal LAB gate | P1 | true/false/pwd/git/signal/timeout/long-output; transport status separate |
| 4 | Resource not found / stale tool registration after reconnect | PARTIAL: capability generations + M0 smoke exist, registry generation not exposed | rediscovery/doctor discipline | Implement typed stale handle + generation | P1 | discover->bind->generation; reconnect refresh; one tool failure != all tools disabled |
| 5 | Controller/session/conversation identity confusion and wrong re-entry target | LAB STRONGER | negative-test catalog only | Keep LAB machine identity | P0 gate | prompt claim, active tab, old lineage, stale lease and wrong target fail closed |
| 6 | Prompt typed but not sent; duplicate re-entry; retry storm | STRONG / FINAL VERIFY: transactional Host submit + tombstones + exact rollback | one-send/uncertainty-stop | Keep LAB; formalize legacy gate | P0 | prepared != submitted; same-wake concurrency; post-submit proof; bounded retry |
| 7 | Child terminal fails to resume SAME Controller | PARTIAL / FINAL BLOCKER | completion-gate idea | Finish existing Runtime chain | P0 | terminal->canonical assignment->same Controller wake->consume->project-wide recompute->CLOSED |
| 8 | Old supervisor overwrites new receipt/token/generation | STRONG Runtime lineage | no source stronger | Keep LAB | P0 gate | interleaving; side-effect-time revalidation; superseded worker no-side-effect exit |
| 9 | UI selector change breaks submit; page existence treated as terminal/identity | PARTIAL: Host identity strong; WEX selector/recovery open | structured result/fail-closed transfer | Borrow invariants/tests | P1 | selector abstraction; reload/restart recovery; page-exists != terminal; DOM != identity |
| 10 | Connector/auth token expires or drifts | PARTIAL: local capability TTL/revoke strong; public Connector OAuth not productized | OAuth 2.1/PKCE/rotation/revoke/pairing | Selective implementation | P1 | expiry/refresh/rotation/revoke/wrong-workspace/pairing replay |
| 11 | Multiple workspaces/projects cross-wire | STRONG local authorization, E2E GAP | workspace-bound token/connector lifecycle | Add concurrent E2E | P1 | two workspaces; distinct connector/session/capability/jobs/files/browser/computer |
| 12 | Job lifecycle lost after reconnect/restart | STRONG current core, restart identity contract gap | doctor/recovery classification | Add formal restart semantics | P1 | incremental output; success/fail/cancel/timeout/process-gone; recover or explicit LOST |
| 13 | VPN/network switch temporarily fixes stale state | MISSING formal fault injection | tunnel doctor + stable hostname | Implement after endpoint generation | P1 | Wi-Fi/VPN/IP/sleep/tunnel restart; no silent stale |
| 14 | Large prompt/command hangs; policy denial confused with bridge crash | PARTIAL size/time budgets; product-wide policy state not unified | bounded context packet | Add limits/state tests | P1 | size/stream/cancel/timeout; POLICY_DENIED distinct from transport/process failure |
| 15 | No one-command diagnosis/self-healing | MISSING -> P0 STARTED | doctor | Incremental LAB doctor | P0/P1 | one command reports layers; safe repair idempotent; minimum user action |
| 16 | New LAB silently falls back to old AI-Bridge | Architecturally forbidden, proof incomplete | explicit fail-closed path | Add execution-path provenance | P1 | no silent fallback; final old-AI-Bridge-off E2E |
| 17 | Web result not strongly bound to assignment | PARTIAL WEX-01 correlation; canonical acceptance open | packet/result/receipt/complete gate | Map to WEX-02 | P1 | assignment/execution/attempt/conversation/result/terminal binding |
| 18 | Completion inferred from visible Web output | PARTIAL: UI evidence exists; trusted terminal source open | deterministic complete gate | Borrow invariant/tests | P1 | no terminal without terminal contract; UI evidence remains partial |

## Gap Matrix

| Capability / failure class | LAB state | GitHub implementation to borrow | Fusion decision | Priority | Evidence target |
| --- | --- | --- | --- | --- | --- |
| Layered doctor | P0 IMPLEMENTED | codex-with-chatgpt doctor | Adopted concept with LAB-native probes | P0 | 7/7 doctor tests + live M0 HEALTHY |
| Tunnel endpoint generation | PARTIAL: canonical private runtime metadata + generation + dual-active fail-closed implemented; public Connector endpoint binding remains | Quick/Named repair state | Extend LAB contract, not source UX | P1 | 10/10 M0 tests + no-restart live adoption; next Connector stale/repair E2E |
| Connector binding/repair | Missing machine probe | workspace repair-needed state | Build explicit Connector state | P1 | endpoint-binding E2E |
| OAuth/session lifecycle | Local auth strong; Connector OAuth missing | OAuth 2.1/PKCE/DCR/rotation/revoke/pairing | Selectively implement | P1 | auth fault matrix |
| Workspace isolation | Strong | token/workspace binding reinforces design | Keep LAB; add multi-workspace E2E | P1 | 2-workspace concurrency |
| Shell process semantics | Strong implementation; formal legacy suite missing | none needed | Keep LAB | P1 | exit-code integrity suite |
| Job lifecycle | Strong core; restart semantics gap | recovery classification | Extend LAB tests | P1 | restart recover-or-LOST |
| Controller identity | LAB stronger | none | Do not replace | P0 gate | wrong-session negative suite |
| Host re-entry exactly-once | LAB stronger / final Live-E2E open | one-send/uncertainty-stop reinforces it | Keep LAB | P0 | confirmed SAME Controller wake |
| Web result packet | Partial | context/result/receipt | Map to WEX-02 | P1 | Runtime acceptance rejects mismatch |
| Completion gate | Partial | deterministic complete | Map to WEX-02/03 | P1 | page-visible != terminal |
| Browser recovery | Partial | browser-session projects as patterns | Borrow recovery tests, not identity | P1 | reload/restart/timeout/cancel |
| Old path migration | M0 preview ready | neither source authoritative | LAB-specific execution_path proof | P1 | old AI-Bridge off |

## P0 order — must not delay Host/GOV Live-E2E

1. Keep Host/GOV final SAME-Controller Live-E2E as the critical path.
2. Land this source audit and Legacy Failure Matrix.
3. Land read-only layered lab_doctor diagnostics.
4. Treat current Host identity/exactly-once/supersession gates as mandatory legacy regressions.

## P1 order — immediately after Host/GOV closure

1. Endpoint generation + tunnel/Connector binding state.
2. doctor --repair for safe, idempotent, workspace-scoped repairs only.
3. OAuth 2.1 / PKCE / pairing / refresh rotation / revoke at the public Connector boundary.
4. Web Executor result packet / terminal receipt / completion gate.
5. Shell process-semantic legacy suite and job restart semantics.
6. Two-workspace concurrent E2E.
7. VPN/network/sleep fault injection.
8. Explicit execution-path provenance and old-AI-Bridge-off acceptance.

## Failover / recovery philosophy

LAB does not promise that nothing ever disconnects. It promises that failures are localized, auditable and recoverable to the last trusted state.

- every mutable connection or execution identity has a generation or immutable attempt identifier;
- retries are bounded and classified;
- pre-side-effect retry requires authoritative revalidation;
- post-side-effect uncertainty is never blindly retried;
- stale session, active-tab, old tunnel, random workspace and old-AI-Bridge fallback are forbidden;
- auth never silently downgrades;
- repair actions are idempotent and workspace-scoped;
- unrecoverable states return minimum user action rather than restart-everything guidance;
- policy rejection is not a Bridge crash and must not be bypassed by encoding or command fragmentation.

## P0 live evidence — 2026-09-09

The first robustness migration gate found a real worktree/runtime-state bug and closed it without restarting the execution path:

- before: the same healthy tunnel was visible from main but a secondary worktree reported tunnel=false because PID/profile state lived under the source tree;
- repair: M0 tunnel process/profile state now has a canonical private runtime representation under the M0 runtime root, with endpoint_generation and explicit state_source;
- discovery is read-only; multiple matching active tunnels fail closed instead of implicit selection;
- safe adoption copies only the private tunnel profile into private runtime state and does not restart a healthy tunnel;
- real gate: bridge PID 84489 unchanged; tunnel PID 15767 unchanged; state_source moved live_process_discovery -> canonical_runtime_meta; endpoint_generation=1;
- canonical files tunnel.json, tunnel.pid and tunnel/local-agent-bridge-poc.yaml are 0600;
- lab_doctor --scope m0 reports HEALTHY after adoption;
- full doctor remains DEGRADED by design until Connector/browser/Computer/Web Executor/Runtime adapter probes are instrumented.

This evidence is the intended legacy-regression behavior: a per-layer state problem is diagnosed and safely repaired without declaring the whole Bridge dead or requiring a global restart.
