# Production Connector P0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Converge Host current-entry attestation, secure Files/Shell Bridge, tunnel publication, and ChatGPT private connector into one fail-closed production release chain.

**Architecture:** Keep the historical M0 PoC lifecycle intact but non-production. Add a canonical production contract pinned to `/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge`, an immutable release revision and endpoint generation, route `discover_current_entry` through a dedicated Host MCP backend, and start a separate production runtime/socket/profile only after Host/Bridge/manifest fences agree.

**Tech Stack:** Python 3 unittest/runtime launcher, Node.js native Host/MCP adapter, Rust Local Tool Bridge, OpenAI tunnel-client, hardened CatDesk local backend.

**Spec:** User-approved P0 repair contract in the current conversation.

## Global Constraints

- Do not modify Adaptive Agent Runtime.
- Do not create, replace, fork, or impersonate a Controller.
- Existing logical Controller remains `a697051d-402b-4062-a883-a29ba8b395e9`.
- `discover_current_entry` must not accept caller `conversation_id`.
- Production workspace is `/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge`.
- Historical `/private/tmp/lab-catdesk-candidate-e2e/workspace` cannot be a production default.
- Host / Bridge / connector manifest / endpoint generation mismatch fails closed.
- Final acceptance requires fresh Personal Chat Live-E2E, not unit tests alone.

---

### Task 1: Production contract and workspace fence

**Files:**
- Modify: `tests/test_production_connector.py`
- Modify: `scripts/production_connector.py`

- [ ] Add RED assertions for exact canonical workspace, production runtime/profile isolation, release revision, endpoint generation, and stale mismatch rejection.
- [ ] Run Python test and verify expected failure.
- [ ] Implement the minimum production contract/fence functions.
- [ ] Re-run Python test GREEN.

### Task 2: Host and Bridge release fence

**Files:**
- Modify: `experiments/web-host-attestation/chrome-host/host-mcp-adapter.test.mjs`
- Modify: `experiments/web-host-attestation/chrome-host/host-mcp-adapter.mjs`
- Modify: `crates/local-tool-bridge/src/main.rs`
- Modify: `crates/local-tool-bridge/src/lib.rs`
- Modify: `crates/local-tool-bridge/tests/ingress.rs`

- [ ] Add RED tests requiring the Host adapter and Bridge to reject missing/mismatched production contract revision/generation before serving.
- [ ] Run targeted Node/Rust tests and observe failure.
- [ ] Add minimal manifest/release fence checks and keep `conversation_id` excluded.
- [ ] Re-run targeted tests GREEN.

### Task 3: Production runtime and historical M0 isolation

**Files:**
- Modify: `tests/test_production_connector.py`
- Modify: `scripts/production_connector.py`

- [ ] Add RED tests for production CatDesk/Bridge/Host env, separate socket/profile/runtime root, and refusal to adopt the PoC profile.
- [ ] Run RED.
- [ ] Implement production install/start/status/smoke helpers with private files and immutable release metadata.
- [ ] Generate a production tunnel profile from the existing tunnel control-plane reference while changing only the local MCP socket/profile lifecycle.
- [ ] Stop only the verified historical M0 tunnel/bridge when switching to production; preserve its files as historical evidence.
- [ ] Re-run tests GREEN.

### Task 4: Release, regression, and Live-E2E

**Files:**
- Candidate files from Tasks 1-3 only, plus release/checkpoint metadata if needed.

- [ ] Run Host baseline and current-entry/Host MCP tests.
- [ ] Run Local Tool Bridge full related tests.
- [ ] Run Python production connector tests and historical M0 tests.
- [ ] Commit immutable candidate revision.
- [ ] Install both normal Chrome and Chrome for Testing Native Messaging manifests to the same candidate release.
- [ ] Start production Host MCP, hardened CatDesk backend, Local Tool Bridge, and production tunnel; verify handshake/status/tools.
- [ ] Confirm old M0 process/profile is not the active production target.
- [ ] Refresh/reconnect ChatGPT private connector only if required after local proof is complete.
- [ ] Open a fresh ordinary Personal Chat and prove production tool surface contains `discover_current_entry` + Files/Shell.
- [ ] Call `discover_current_entry` using only generation fences and prove caller `conversation_id` rejection.
- [ ] Use machine attestation for SAME-Controller successor binding without creating a second Controller; verify formal project root read.
