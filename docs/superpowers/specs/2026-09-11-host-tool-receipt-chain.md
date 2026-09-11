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
5. The Bridge removes the capability from arguments before forwarding to CatDesk. The capability binds both the outer request digest and the forwarded request digest. CatDesk remains unchanged.
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
  "capability_id": "hic_<random>",
  "bridge_call_id": "bc_<random>",
  "transport_binding_id": "tb_<random>",
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

Bridge returns a structured `HOST_INVOCATION_CAPABILITY_REQUIRED` result without calling CatDesk. The retried request must be byte-equivalent after removing `host_invocation_capability`. Before consumption, Host re-observes the active generation and requires the same conversation, browser target, top frame, loader, generation anchor, secure origin, extension binding, and extension instance epoch.

## Tool receipts

Capability consumption yields `lab_host_tool_pre_receipt_v1`, binding the capability, exact request digests, Bridge-generated `host_tool_execution_id`, Host entry and epoch, and a fresh single-use pre nonce. Runtime must return `ALLOW` with the same execution ID before Bridge persists `DISPATCH_INTENT`.

After CatDesk returns, Bridge buffers the exact HTTP status and body, hashes them, persists `BACKEND_RESPONSE`, and requests `lab_host_tool_terminal_receipt_v1`. The terminal receipt binds the pre receipt ID and digest, execution ID, request digests, backend route, HTTP status, response body SHA-256, terminal classification, and Host epoch. Runtime must validate the guard evidence file and atomically move its canonical receipt record to CLOSED before Bridge returns a successful protected-tool response.

## Durable states

Bridge and Host journals use the same monotonic lifecycle:

```text
ISSUED -> HOST_VALIDATED_IN_FLIGHT -> RUNTIME_ALLOWED -> DISPATCH_INTENT
       -> BACKEND_RESPONSE -> TERMINAL_PENDING -> TERMINAL
```

`ISSUED` may expire without dispatch. Any state at or after `DISPATCH_INTENT` is never automatically re-executed. Re-submitting the same capability is `REPLAY`; a byte-different request is `MISMATCH`. A persisted pre from a prior Host or extension epoch cannot become successful terminal evidence.

## Acceptance

Code acceptance requires RED-to-GREEN negative tests for caller identity injection, exact-request mismatch, cross-transport use, concurrent consume, TTL, replay after restart, target/frame/loader/generation/extension drift, pre rejection with zero backend calls, and post-dispatch unknown outcomes.

Production acceptance additionally requires an immutable LAB candidate, an exact installed Runtime candidate, pinned mutual adapter hashes, unchanged AI-Bridge/compute-helper health, a fresh Personal Chat `discover_current_entry`, the two-call protected `run_command` handshake, one clean ordered `PreToolUse -> terminal -> CLOSED` cycle, SAME-Controller successor binding, and a separate `accept-live-e2e` receipt. Until that final sequence is observed, P0 remains incomplete.
