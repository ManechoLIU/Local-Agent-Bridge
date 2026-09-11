# Host Tool Receipt Producer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce durable Host-signed capability, pre-tool, and terminal receipts at the real Rust Bridge execution boundary without changing CatDesk or the public Host tool list.

**Architecture:** The Host owns machine observation, signing, and single-use capability state; the Rust Bridge owns transport binding, exact request canonicalization, dispatch ordering, and crash-safe execution state. Protected `run_command` calls use a two-call challenge handshake; the first call issues a bound capability without backend execution and the exact retry consumes it before Runtime authorization and backend dispatch.

**Tech Stack:** Rust/Tokio/Axum/Serde, Node.js ES modules, Chrome extension Native Messaging, Python production release tooling, Node test runner, Cargo tests, Python unittest.

**Spec:** `/Users/echoman/Documents/ChatGPT/.worktrees/lab-production-connector-p0/docs/superpowers/specs/2026-09-11-host-tool-receipt-chain.md`

## Global Constraints

- Public Host MCP `tools/list` remains exactly `discover_current_entry`.
- Caller identity fields and caller request/session IDs are never identity evidence.
- `bridge_call_id`, `bridge_challenge_nonce`, `transport_binding_id`, and `host_tool_execution_id` are generated inside Bridge/Host, never accepted from tool arguments or incoming headers.
- The capability uses `request_digest_profile=lab_run_command_digest_v1` and binds exact normalized `run_command` arguments, canonical workspace, the first/challenge raw outer SHA-256, frozen capability-neutral forwarded SHA-256, production release revision/generation, Bridge capability context, machine-derived Web entry, active generation anchor, and loaded extension epoch. Pre/terminal additionally bind the retry raw outer SHA-256.
- Capability and dispatch state are atomically persisted before side effects; post-dispatch uncertainty is terminally `RESULT_UNKNOWN_AFTER_DISPATCH` and never auto-retried.
- CatDesk source and binary are unchanged; Bridge strips `host_invocation_capability` before forwarding.
- LAB remains independently runnable without Runtime integration. Production protected shell calls fail closed when the explicitly configured, hash-pinned Runtime hook is unavailable.
- AI-Bridge and compute helper remain available and unchanged, but their data never satisfies Host identity, Runtime CLOSED, or Live-E2E gates.
- Existing untracked `.orig`, `.rej`, and `__pycache__` files are preserved.

---

### Task 1: Host capability and signed receipt state machine

**Files:**
- Modify: `experiments/web-host-attestation/attestation.mjs`
- Modify: `experiments/web-host-attestation/attestation.test.mjs`
- Modify: `experiments/web-host-attestation/chrome-host/extension/current-entry-registry.js`
- Modify: `experiments/web-host-attestation/chrome-host/current-entry-registry.test.mjs`
- Modify: `experiments/web-host-attestation/chrome-host/extension/background.js`
- Modify: `experiments/web-host-attestation/chrome-host/native-host-core.mjs`
- Modify: `experiments/web-host-attestation/chrome-host/native-host-core.test.mjs`
- Modify: `experiments/web-host-attestation/chrome-host/native-host.mjs`
- Modify: `experiments/web-host-attestation/chrome-host/native-host-process.test.mjs`

**Interfaces:**
- Consumes: private Native Messaging channel, Host HMAC secret, current extension epoch, `currentEntryRegistry.resolveCurrent()`.
- Produces: `ToolInvocationReceiptJournal.issueCapability(input)`, `.consumeCapability(input)`, and `.terminalize(input)` returning the exact v1 schemas in the spec; private browser/native operations `tool_receipt_issue`, `tool_receipt_consume`, and `tool_receipt_terminal`.

- [ ] **Step 1: Write failing Host receipt tests**

Add behavior tests whose literal fixtures prove: machine-only identity fields; exact normalized request binding; generation-anchor and extension-epoch binding; single-use consume across restart; cross-transport, TTL, target/frame/loader/generation drift rejection; terminal requires the exact consumed pre and retry outer digest; terminal does not store response bodies.

```js
assert.equal(capability.provenance, "lab_host_invocation_capability_v1");
assert.equal(pre.provenance, "lab_host_tool_pre_receipt_v1");
assert.equal(terminal.provenance, "lab_host_tool_terminal_receipt_v1");
assert.throws(() => restarted.consumeCapability(changed), /replay|mismatch/i);
```

- [ ] **Step 2: Run the focused tests and capture RED**

Run:

```bash
node --test experiments/web-host-attestation/attestation.test.mjs experiments/web-host-attestation/chrome-host/extension/current-entry-registry.test.mjs experiments/web-host-attestation/chrome-host/native-host-core.test.mjs experiments/web-host-attestation/chrome-host/native-host-process.test.mjs
```

Expected: FAIL because the new receipt journal and private operations do not exist; existing tests remain green before the first new failure.

- [ ] **Step 3: Implement the minimal Host state machine**

Use canonical JSON HMAC, a cross-instance lock plus reload-before-mutate CAS, private atomic state writes with file and parent-directory fsync, strict owner/symlink/mode checks, and the Host-owned monotonic state transitions from the spec. `generation_anchor_sha256` is computed from the exact sorted active Chrome generation request IDs and is never returned as raw request IDs. Exact expiry (`now == expires_at_unix_ms`) is expired.

```js
export class ToolInvocationReceiptJournal {
  issueCapability(input) {}
  consumeCapability(input) {}
  terminalize(input) {}
}
```

The extension returns machine context and a generation-anchor digest only. Native Host adds the loaded extension epoch and signs all receipts.

- [ ] **Step 4: Run focused Host tests GREEN**

Run the Step 2 command. Expected: all tests pass with no warnings.

- [ ] **Step 5: Commit Task 1**

```bash
git add experiments/web-host-attestation/attestation.mjs experiments/web-host-attestation/attestation.test.mjs experiments/web-host-attestation/chrome-host/extension/current-entry-registry.js experiments/web-host-attestation/chrome-host/extension/current-entry-registry.test.mjs experiments/web-host-attestation/chrome-host/extension/background.js experiments/web-host-attestation/chrome-host/native-host-core.mjs experiments/web-host-attestation/chrome-host/native-host-core.test.mjs experiments/web-host-attestation/chrome-host/native-host.mjs experiments/web-host-attestation/chrome-host/native-host-process.test.mjs
git commit -m "feat(host): sign durable tool execution receipts"
```

### Task 2: Bridge challenge handshake and dispatch journal

**Files:**
- Modify: `crates/local-tool-bridge/Cargo.toml`
- Modify: `crates/bridge-security-core/src/lib.rs`
- Modify: `crates/local-tool-bridge/src/lib.rs`
- Modify: `crates/local-tool-bridge/src/main.rs`
- Modify: `crates/local-tool-bridge/tests/ingress.rs`
- Create: `crates/local-tool-bridge/tests/tool_receipt_chain.rs`

**Interfaces:**
- Consumes: Task 1 private Host operations and signed schemas.
- Produces: Bridge-generated transport/execution identities, `ToolReceiptJournal`, protected `run_command` schema decoration, capability stripping, private Host client, and optional hash-pinned Runtime hook protocol `runtime_host_tool_hook_v1`.

- [ ] **Step 1: Write failing Bridge tests**

Test the real ingress boundary with controlled Host/backend/hook servers. The first protected call must return `HOST_INVOCATION_CAPABILITY_REQUIRED` and make zero backend calls. The exact retry uses a distinct caller JSON-RPC ID, must call Host consume, call Runtime pre, persist dispatch intent, execute the backend once with the stable Bridge ID and no capability, call Host terminal, call Runtime terminal, map the response back to the retry caller ID, and return the buffered response.

```rust
assert_eq!(backend.calls(), 0);
assert_eq!(challenge["error"]["data"]["code"], "HOST_INVOCATION_CAPABILITY_REQUIRED");
assert_eq!(backend.calls(), 1);
assert_eq!(journal.state(call_id), "TERMINAL");
```

Add negative tests for caller-supplied execution IDs, changed command/cwd/timeout, cross-binding use, concurrent consume, state persistence failure, pre rejection, backend timeout, Host terminal loss, Runtime terminal rejection, restart after `DISPATCH_INTENT`, duplicate JSON keys, malformed/null capability, retry outer digest substitution, backend response-ID mismatch, and proof/body mismatch.

- [ ] **Step 2: Run the focused Cargo tests and capture RED**

Run:

```bash
cd crates/local-tool-bridge
cargo test --test tool_receipt_chain --test ingress
```

Expected: FAIL because the receipt client/journal and schema decoration are absent.

- [ ] **Step 3: Implement the minimal Bridge flow**

Add a Bridge-only execution identity to `RequestProof` and its fixed HMAC message. Strictly parse the protected JSON-RPC profile, canonicalize `run_command` after removing only `host_invocation_capability`, replace the backend envelope ID with `bridge_call_id`, freeze those exact bytes, and bind the challenge outer, frozen forwarded, normalized arguments, and retry outer digests under `lab_run_command_digest_v1`. Sign the backend RequestProof only after the frozen forwarded bytes exist. Buffer and validate backend responses before terminalization, then map only the response ID for the caller after Runtime closure.

Receipt behavior and schema decoration are conditional on the receipt chain being configured; the disabled path preserves legacy request/response bytes and never advertises or forwards the reserved capability. Journal mutation takes a cross-process file lock and reloads durable state inside that lock before every insert/update. Private UDS connection, write, and read all have real deadlines; use Tokio's async I/O/time features rather than a blocking connect with only post-connect read/write timeouts.

```rust
pub struct BufferedBackendResponse {
    pub status: StatusCode,
    pub content_type: Option<String>,
    pub bytes: Bytes,
}

pub enum ToolReceiptState {
    Issued,
    HostValidatedInFlight,
    RuntimeAllowed,
    DispatchIntent,
    BackendResponse,
    TerminalPending,
    Terminal,
    ResultUnknownAfterDispatch,
}
```

`tools/list` keeps the same tool names and only decorates `run_command.inputSchema` with the reserved capability field. The outgoing CatDesk body never contains that field.

- [ ] **Step 4: Run focused Bridge tests GREEN**

Run the Step 2 command plus:

```bash
cargo test --test release_fence --test runtime_recovery
```

Expected: all tests pass and no backend side effect appears on any failing pre path.

- [ ] **Step 5: Commit Task 2**

```bash
git add crates/local-tool-bridge/Cargo.toml crates/bridge-security-core/src/lib.rs crates/local-tool-bridge/src/lib.rs crates/local-tool-bridge/src/main.rs crates/local-tool-bridge/tests/ingress.rs crates/local-tool-bridge/tests/tool_receipt_chain.rs
git commit -m "feat(bridge): gate shell dispatch on Host receipts"
```

### Task 3: Runtime verifier v2 receipt commands

**Files:**
- Modify: `experiments/web-host-attestation/attestation.mjs`
- Modify: `experiments/web-host-attestation/attestation.test.mjs`
- Modify: `experiments/web-host-attestation/chrome-host/client.mjs`
- Modify: `experiments/web-host-attestation/chrome-host/client.test.mjs`
- Modify: `experiments/web-host-attestation/chrome-host/integration.mjs`
- Modify: `experiments/web-host-attestation/chrome-host/integration.test.mjs`
- Modify: `experiments/web-host-attestation/chrome-host/native-host-core.test.mjs`
- Modify: `experiments/web-host-attestation/chrome-host/runtime-verifier-cli.mjs`
- Modify: `experiments/web-host-attestation/chrome-host/runtime-verifier-cli.test.mjs`

**Interfaces:**
- Consumes: Task 1 signed pre/terminal schemas, Host verification key, current extension binding, and the private current-entry channel.
- Produces: verifier protocol `runtime_host_verifier_cli_v2` operations `verify_tool_pre` and `verify_tool_terminal`, each returning an exact immutable receipt digest plus freshly verified machine context and never returning the Host key.

- [ ] **Step 1: Write failing verifier boundary tests**

Use literal Task 1 receipts and the real CLI subprocess. Prove exact schema/profile/MAC, bounded freshness, current extension binding/instance/load epoch from the same fresh current-entry request, current-entry conversation and generation anchor, all four request digests, execution/pre-chain IDs, terminal outcome and response digest. Reject unknown keys, old unprofiled receipts, forged or stale MACs, wrong current entry, changed pre/terminal chain, and caller-added Runtime Controller/turn fields.

- [ ] **Step 2: Run focused tests and capture RED**

Run:

```bash
node --test experiments/web-host-attestation/attestation.test.mjs \
  experiments/web-host-attestation/chrome-host/integration.test.mjs \
  experiments/web-host-attestation/chrome-host/runtime-verifier-cli.test.mjs
```

Expected: FAIL because verifier v2 receipt commands do not exist.

- [ ] **Step 3: Implement minimal verifier v2**

Export pure Host receipt authentication helpers from `attestation.mjs`, then expose them through the existing immutable CLI/integration boundary. Add a backward-compatible client method that returns current entry and extension epoch from the same private Host response while preserving the legacy method shape. Reuse the configured private Host key and loaded-extension binding checks. The CLI reads one bounded JSON object and writes one bounded object; it does not accept caller identity as attestation, does not mutate Host journal state, and does not invoke a shell.

`generation_anchor_sha256` is an additive Host-signed field on `runtime_host_current_entry_v1`: validation, signing, and verification require it so the verifier can compare the receipt with the same fresh browser generation. Existing v1 operation inputs, provenance, required fields, and result envelopes stay compatible; consumers that already authenticate the entire object continue to cover the additive field.

```json
{
  "protocol": "runtime_host_verifier_cli_v2",
  "operation": "verify_tool_pre",
  "receipt_sha256": "<64 hex>",
  "receipt": {"provenance": "lab_host_tool_pre_receipt_v1"},
  "expected_target_generation": 7,
  "expected_ownership_generation": 9,
  "expected_request_digests": {
    "outer_request_sha256": "<64 hex>",
    "forwarded_request_sha256": "<64 hex>",
    "normalized_request_sha256": "<64 hex>",
    "retry_outer_request_sha256": "<64 hex>"
  }
}
```

The verifier obtains `current_entry` and `extension_epoch` itself from one fresh private Host request. It rejects any caller-supplied current entry, Controller identity, Runtime turn identity, or unknown envelope key. The terminal operation additionally requires an exact `expected_pre_chain` containing `pre_receipt_id`, `pre_receipt_sha256`, `capability_id`, and `host_tool_execution_id`.

- [ ] **Step 4: Run focused verifier tests GREEN**

Run the Step 2 command plus the complete Task 1 Host suite. Expected: all pass and existing v1 current-entry/re-entry commands remain compatible.

- [ ] **Step 5: Commit Task 3**

```bash
git add experiments/web-host-attestation/attestation.mjs experiments/web-host-attestation/attestation.test.mjs experiments/web-host-attestation/chrome-host/client.mjs experiments/web-host-attestation/chrome-host/client.test.mjs experiments/web-host-attestation/chrome-host/integration.mjs experiments/web-host-attestation/chrome-host/integration.test.mjs experiments/web-host-attestation/chrome-host/native-host-core.test.mjs experiments/web-host-attestation/chrome-host/runtime-verifier-cli.mjs experiments/web-host-attestation/chrome-host/runtime-verifier-cli.test.mjs
git commit -m "feat(host): verify tool receipts for Runtime"
```

### Task 4: Bridge-to-Runtime intent and result preimages

**Files:**
- Modify: `crates/local-tool-bridge/src/lib.rs`
- Modify: `crates/local-tool-bridge/tests/tool_receipt_chain.rs`

**Interfaces:**
- Consumes: Task 2 protected request canonical bytes, buffered CatDesk response bytes, Task 1 Host-signed normalized request and response digests, and Task 3 verifier-v2 semantics.
- Produces: exact private `runtime_host_tool_intent_v1` on pre and terminal plus `runtime_host_backend_response_v1` on terminal; neither object is identity evidence until Runtime verifies its bytes against the Host-signed digest.

- [ ] **Step 1: Write failing exact-envelope tests**

Capture real Runtime-hook requests. Require pre and terminal to carry the same bounded UTF-8 canonical normalized request bytes, prove their SHA-256 equals the Host receipt's `normalized_request_sha256`, and prove the canonical object contains only `tool_name: run_command` plus capability-neutral arguments. Terminal additionally carries the exact buffered CatDesk JSON-RPC response bytes; its SHA-256 must equal the terminal receipt's `response_body_sha256` and its structured result must preserve `success`, `exitCode`, `timedOut`, `command`, and `cwd`. Add mutation, oversize, unknown-key, response-error, and retry-ID mapping regressions.

- [ ] **Step 2: Run focused tests and capture RED**

Run:

```bash
cd crates/local-tool-bridge
cargo test --test tool_receipt_chain
```

Expected: the new exact-envelope assertions fail because the private Runtime calls currently carry receipts but not their authenticated preimages.

- [ ] **Step 3: Implement minimal transport**

Serialize the already-produced canonical normalized bytes as exact UTF-8 text; do not reserialize caller input and do not add identity fields. Send the same intent object in pre and terminal. After Bridge's existing strict backend JSON-RPC validation, send the exact buffered response bytes in the terminal response object. Keep the one-shot five-second private UDS deadline, 64 KiB envelope limit, durable no-retry lifecycle, frozen CatDesk request, and final caller-ID mapping unchanged. No Host schema, public MCP tool, Runtime code, or CatDesk code changes belong to this task.

- [ ] **Step 4: Run focused and regression tests GREEN**

Run Task 2 Cargo tests, `bridge-security-core`, and `computer-runtime`. Expected: all pass, receipt-disabled mode remains byte-compatible, and any Runtime pre/terminal failure after dispatch preserves the existing fail-closed/unknown semantics.

- [ ] **Step 5: Commit Task 4**

```bash
git add crates/local-tool-bridge/src/lib.rs crates/local-tool-bridge/tests/tool_receipt_chain.rs
git commit -m "fix(bridge): bind Runtime hook to exact tool result"
```

### Task 5: Production release wiring and compatibility fences

**Files:**
- Modify: `scripts/production_connector.py`
- Modify: `tests/test_production_connector.py`
- Modify: `experiments/web-host-attestation/chrome-host/host-mcp-adapter.test.mjs`

**Interfaces:**
- Consumes: Task 1 Host state path/operations, Task 2 and Task 4 Bridge env/hook settings, and Task 3 verifier v2 commands.
- Produces: immutable release paths for Host receipt state, Bridge receipt journal, Host socket and verification material, and a Runtime hook registration that pins executable, transitive bundle hashes, installed Runtime revision, and protocol `runtime_host_tool_hook_v1`.

- [ ] **Step 1: Write failing production contract tests**

Add literal assertions that the immutable release carries the new Host/Bridge state paths and that protected shell activation rejects missing, symlinked, group-writable, wrong-owner, stale-revision, or hash-mismatched Runtime hooks. Preserve the public Host single-tool assertion and the exact AI-Bridge/compute-helper before/after health identity.

```python
self.assertEqual(host_tools, ["discover_current_entry"])
self.assertEqual(binding["protocol"], "runtime_host_tool_hook_v1")
self.assertFalse(binding["ai_bridge_is_trusted_receipt_source"])
```

- [ ] **Step 2: Run focused production tests and capture RED**

Run:

```bash
python3 -m unittest -v tests.test_production_connector
node --test experiments/web-host-attestation/chrome-host/host-mcp-adapter.test.mjs
```

Expected: new contract assertions fail before implementation; all existing assertions remain green.

- [ ] **Step 3: Implement release wiring**

Generate private state paths under the immutable production runtime, pass only exact canonical paths through component environments, include the new executable and transitive files in release hashes, and validate the Runtime hook immediately before every production start. Do not start, stop, install, refresh, or reconnect production in this task.

- [ ] **Step 4: Run the production and focused cross-component regression**

Run the Step 2 command, Task 1 and Task 3 Node commands, and Task 2 Cargo commands. Expected: all pass with unchanged public tool names and unchanged AI-Bridge/compute-helper identity checks.

- [ ] **Step 5: Commit Task 5**

```bash
git add scripts/production_connector.py tests/test_production_connector.py experiments/web-host-attestation/chrome-host/host-mcp-adapter.test.mjs
git commit -m "feat(production): pin Host tool receipt chain"
```
