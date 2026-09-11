# Host Tool Receipt Chain Specification

## Authority and scope

This specification records the user's 2026-09-11 authorization to extend the existing Local Agent Bridge P0 repair into Adaptive Agent Runtime where required to close the Host/Bridge receipt loop. It supersedes only the earlier attachment's prohibition on modifying Adaptive Agent Runtime. All other boundaries remain in force.

The unique logical Controller remains unchanged. No implementation may create, fork, replace, impersonate, or take ownership of a Controller or the LAB main Goal.

## Security claim

Version 1 proves a Host-signed, machine-derived, current Web-entry authorization bound to one Bridge transport invocation and one exact tool request. It does not claim an OpenAI-signed request identity, ChatGPT user principal, model identity, or resistance to a hostile process running as the same macOS UID.

Official OpenAI documentation currently establishes OAuth/access-token and configured-header authentication for remote MCP servers, but does not document a request-level signed conversation or tool-call token delivered to a private MCP server. The product must not invent such a claim.

## Global invariants

1. The public Host MCP tool surface remains exactly `discover_current_entry`; private receipt operations never appear in `tools/list`.
2. Caller-provided conversation, target, frame, loader, generation anchor, Host epoch, request ID, JSON-RPC ID, `Mcp-Session-Id`, or `x-openai-*` data is never identity evidence.
3. The Bridge generates `bridge_call_id`, `bridge_challenge_nonce`, and `transport_binding_id`. The Host derives conversation, browser target, top frame, loader, secure origin, active generation anchor, extension binding, and extension instance epoch from the local machine.
4. A state-changing or shell tool is not dispatched on its first call. The Host issues a short-lived, single-use capability bound to the exact normalized tool request and current machine entry; the caller must retry the exact request with that capability.
5. The Bridge removes the capability from arguments before forwarding to CatDesk. The capability binds the first/challenge raw outer digest, one frozen capability-neutral backend envelope, and the normalized tool request. Consumption additionally signs the retry raw outer digest. CatDesk remains unchanged.
6. Capability issuance and consumption are persisted before any backend side effect. Only one concurrent consumer can win.
7. Runtime receives only Host-signed pre and terminal receipts. It independently re-derives the unique Controller, canonical repository, active Web target, target generation, ownership generation, and active turn under its canonical registry lock.
8. Runtime closure requires one exact tuple: `(controller_id, host=web, execution_target_session_id, turn_id, target_generation, ownership_generation, bridge_call_id, host_tool_execution_id, normalized_request_sha256, snapshot_sha256)`.
9. `exit_code=None`, stdout substrings, AI-Bridge audit JSONL, DOM, URL, title, screenshot, active-tab state, caller session IDs, and wake confirmation cannot close a control event.
10. Wake confirmation and `accept-live-e2e` remain separate gates. A confirmed wake is never inferred to be a clean CLOSED cycle or Live-E2E acceptance.
11. LAB remains independently runnable when the optional Runtime hook is not configured. In the production Controller profile, protected shell calls fail closed when the pinned Runtime hook is missing, stale, or rejects. AI-Bridge and compute helper remain installed and observable as fallback paths, but never become trusted receipt or identity sources.
12. No automatic retry is allowed after dispatch intent is durable. A crash, timeout, Host/extension epoch change, lost backend response, or missing terminal receipt after dispatch becomes `RESULT_UNKNOWN_AFTER_DISPATCH`.

## Capability handshake

The first protected `run_command` call has no `host_invocation_capability`. Bridge canonicalizes the request excluding that reserved field and asks the private Host channel to issue:

```json
{
  "schema_version": 1,
  "provenance": "lab_host_invocation_capability_v1",
  "request_digest_profile": "lab_run_command_digest_v1",
  "capability_id": "hic_<random>",
  "bridge_call_id": "bc_<random>",
  "bridge_challenge_nonce": "<random>",
  "transport_binding_id": "tb_<random>",
  "workspace": "/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge",
  "production_release_revision": "<40 hex>",
  "production_endpoint_generation": 1,
  "bridge_principal_id": "<configured Bridge principal>",
  "bridge_execution_session_id": "<configured Bridge session>",
  "bridge_capability_id": "<configured Bridge capability>",
  "bridge_workspace_id": "local-agent-bridge-production",
  "tool_name": "run_command",
  "outer_request_sha256": "<64 hex>",
  "forwarded_request_sha256": "<64 hex>",
  "normalized_request_sha256": "<64 hex>",
  "conversation_id": "<machine derived>",
  "browser_target_id": "<machine derived>",
  "top_frame_id": "<machine derived>",
  "loader_id": "<machine derived>",
  "generation_anchor_sha256": "<machine derived 64 hex>",
  "secure_origin": "https://chatgpt.com",
  "extension_binding_sha256": "<64 hex>",
  "extension_instance_id_sha256": "<64 hex>",
  "issued_at_unix_ms": 1,
  "expires_at_unix_ms": 2,
  "host_mac_sha256": "<64 hex>"
}
```

Bridge returns a structured `HOST_INVOCATION_CAPABILITY_REQUIRED` result without calling CatDesk. After the defined capability-neutral projection, the retry must reproduce the exact frozen forwarded bytes and normalized request of the first call. Before consumption, Host re-observes the active generation and requires the same conversation, browser target, top frame, loader, generation anchor, secure origin, extension binding, and extension instance epoch.

For `lab_run_command_digest_v1`, byte equivalence means equality after one explicit Bridge projection, not textual equality of the two raw JSON bodies:

- `outer_request_sha256` is permanently the SHA-256 of the first/challenge call's exact raw body.
- `forwarded_request_sha256` is the SHA-256 of the frozen capability-neutral canonical JSON-RPC envelope whose top-level ID is the Bridge-generated `bridge_call_id`; those exact bytes are later sent to CatDesk.
- `normalized_request_sha256` is the SHA-256 of canonical `{tool_name, arguments}` after removing only the reserved capability argument.
- `retry_outer_request_sha256` is computed from the retry's exact raw body, including the submitted capability and caller correlation ID. It cannot appear in the capability without a circular commitment, so it is required and Host-signed only in the pre and terminal receipts.

The protected profile rejects duplicate JSON keys, batches, notifications, malformed or null capability values, unsupported envelope members, unsafe numeric forms, invalid Unicode, and oversized/deep requests before Host consume. The Bridge is the sole producer of the canonical forwarded and normalized bytes; Host signs their digests and does not independently reserialize tool arguments. Golden byte vectors pin the serializer. Independent caller JSON-RPC IDs are response-correlation data only: each ingress attempt receives a fresh Bridge proof request ID, CatDesk sees the stable `bridge_call_id`, and Bridge validates then maps the buffered backend response ID to the retry caller ID only after terminal closure.

## Tool receipts

Capability consumption yields `lab_host_tool_pre_receipt_v1`, binding the capability, the immutable challenge/forwarded/normalized digests, the mandatory `retry_outer_request_sha256`, Bridge-generated `host_tool_execution_id`, Host entry and epoch, and a fresh single-use pre nonce. Runtime must return `ALLOW` with the same execution ID before Bridge persists `DISPATCH_INTENT`.

After CatDesk returns, Bridge buffers the exact HTTP status and original backend body, validates the stable backend response ID, hashes the original bytes, persists `BACKEND_RESPONSE`, and requests `lab_host_tool_terminal_receipt_v1`. The terminal receipt binds the pre receipt ID and digest, execution ID, all four request digests, backend route, HTTP status, response body SHA-256, terminal classification, and Host epoch. Runtime must validate the guard evidence file and atomically move its canonical receipt record to CLOSED before Bridge maps the response ID and returns a successful protected-tool response.

## Durable states

The combined Host-plus-Bridge journals form one monotonic lifecycle:

```text
ISSUED -> HOST_VALIDATED_IN_FLIGHT -> RUNTIME_ALLOWED -> DISPATCH_INTENT
       -> BACKEND_RESPONSE -> TERMINAL_PENDING -> TERMINAL
```

Host owns and persists `ISSUED`, `HOST_VALIDATED_IN_FLIGHT`, `TERMINAL_PENDING`, and `TERMINAL`. Bridge owns and persists `RUNTIME_ALLOWED`, `DISPATCH_INTENT`, and `BACKEND_RESPONSE`. Neither component fabricates the other's observations or duplicates its state machine.

`ISSUED` may expire without dispatch. Any state at or after `DISPATCH_INTENT` is never automatically re-executed. Re-submitting the same capability is `REPLAY`; a byte-different request is `MISMATCH`. A persisted pre from a prior Host or extension epoch cannot become successful terminal evidence.

## Acceptance

Code acceptance requires RED-to-GREEN negative tests for caller identity injection, exact-request mismatch, cross-transport use, concurrent consume, TTL, replay after restart, target/frame/loader/generation/extension drift, pre rejection with zero backend calls, and post-dispatch unknown outcomes. The real two-call test uses distinct caller JSON-RPC IDs, proves distinct raw outer digests but identical frozen forwarded bytes, verifies the backend proof against those exact bytes, proves the backend sees no capability and the caller receives its retry ID, and rejects duplicate keys or any retry/terminal digest substitution.

Production acceptance additionally requires an immutable LAB candidate, an exact installed Runtime candidate, pinned mutual adapter hashes, unchanged AI-Bridge/compute-helper health, a fresh Personal Chat `discover_current_entry`, the two-call protected `run_command` handshake, one clean ordered `PreToolUse -> terminal -> CLOSED` cycle, SAME-Controller successor binding, and a separate `accept-live-e2e` receipt. Until that final sequence is observed, P0 remains incomplete.
