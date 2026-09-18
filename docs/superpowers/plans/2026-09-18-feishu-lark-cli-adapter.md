# Feishu via LAB / lark-cli Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose Feishu Base and group-chat capabilities through LAB using the official `lark-cli`, with separate read/write capabilities and no AI-Bridge dependency.

**Architecture:** Add a focused `collaboration-adapter` library containing a provider-neutral contract and a bounded `LarkCliProvider`. Wire it into `local-tool-bridge` as an optional backend that contributes `feishu_*` tools to MCP discovery and handles only those fixed operations. `lark-cli` owns OAuth and Feishu API behavior; LAB owns authorization, stable tool schemas, argument validation, routing and result/error normalization.

**Tech Stack:** Rust 2024, Tokio, serde/serde_json, official `@larksuite/cli` binary, existing LAB MCP/authorization infrastructure.

**Spec:** `docs/superpowers/specs/2026-09-18-feishu-lark-cli-adapter-design.md`

## Global Constraints

- LAB is the primary path; do not call or depend on AI-Bridge.
- Do not expose arbitrary `lark-cli api`, arbitrary command paths or shell strings.
- Do not place Feishu tokens/credentials/auth URLs in MCP arguments, logs or model-visible responses.
- Use direct argv execution, never shell interpolation.
- Separate `CollaborationRead` and `CollaborationWrite`.
- No automatic retry after an uncertain write dispatch.
- V1 scope is Base records/schema plus chat/message read/send/reply; no deletion, membership mutation, raw API, attachments or webhooks.
- Feishu remains an external collaboration surface, not canonical project truth.

---

### Task 1: Collaboration adapter contract and bounded process runner

**Files:**
- Create: `crates/collaboration-adapter/Cargo.toml`
- Create: `crates/collaboration-adapter/src/lib.rs`
- Create: `crates/collaboration-adapter/src/feishu.rs`
- Create: `crates/collaboration-adapter/tests/feishu_contract.rs`

**Interfaces:**
- Produces:
  - `enum CollaborationOperation`
  - `struct CollaborationRequest { operation, arguments }`
  - `struct CollaborationResult { provider, backend, operation, identity, data, duration_ms }`
  - `enum CollaborationErrorKind`
  - `struct CollaborationError { kind, message, dispatch_attempted }`
  - async trait `CollaborationProvider::execute(&self, request) -> Result<CollaborationResult, CollaborationError>`
  - `LarkCliProvider::new(cli_path, profile, timeout, output_limit)`

- [ ] **Step 1: Write failing contract tests**

Tests must prove:
- unsupported operation cannot become argv;
- provider command path is executable-only and receives argv without a shell;
- timeout is classified before/after dispatch explicitly;
- stdout over the configured limit is rejected;
- non-JSON provider output is rejected.

- [ ] **Step 2: Run adapter tests and verify RED**

Run:
```bash
cargo test --manifest-path crates/collaboration-adapter/Cargo.toml --test feishu_contract
```

Expected: compile/test failure because the crate/types do not exist yet.

- [ ] **Step 3: Implement minimal contract + injected runner**

Use an injected `CommandRunner` in tests; production runner uses `tokio::process::Command` with `kill_on_drop(true)`, a timeout, bounded stdout/stderr and no shell.

- [ ] **Step 4: Run tests GREEN**

Run the same test command; expected all Task 1 tests PASS.

### Task 2: Feishu read operations

**Files:**
- Modify: `crates/collaboration-adapter/src/feishu.rs`
- Modify: `crates/collaboration-adapter/tests/feishu_contract.rs`

**Interfaces:**
- Operations:
  - `Status`
  - `BaseListTables { base_token }`
  - `BaseListFields { base_token, table_id }`
  - `BaseListRecords { base_token, table_id, view_id?, page_size?, page_token? }`
  - `ChatSearch { query, page_size? }`
  - `MessageList { chat_id, page_size?, page_token? }`

- [ ] **Step 1: Add failing argv-mapping tests**

Expected mappings use only official shortcuts/services and `--format json`, e.g.:
```text
lark-cli base +table-list --base-token <token> --format json
lark-cli base +record-list --base-token <token> --table-id <id> --format json
lark-cli im +chat-search --query <query> --format json --as user
lark-cli im +chat-messages-list --chat-id <id> --format json --as user
```

Status uses the official identity/status surface and sanitizes any token-like fields before returning.

- [ ] **Step 2: Verify RED**

Run adapter tests; expected failures for missing mappings.

- [ ] **Step 3: Implement read mappings and strict validation**

Validate token/id/query lengths and page-size bounds before spawning CLI.

- [ ] **Step 4: Verify GREEN**

Run all adapter tests.

### Task 3: LAB capability model and MCP tool discovery

**Files:**
- Modify: `crates/bridge-security-core/src/lib.rs`
- Modify: `crates/local-tool-bridge/Cargo.toml`
- Modify: `crates/local-tool-bridge/src/lib.rs`
- Modify: `crates/local-tool-bridge/src/main.rs`
- Create: `crates/local-tool-bridge/tests/feishu_integration.rs`

**Interfaces:**
- Add capabilities:
  - `Capability::CollaborationRead`
  - `Capability::CollaborationWrite`
- `ToolPolicy::catdesk_default()` maps read/write `feishu_*` tools to the corresponding capability.
- `BridgeIngress::with_collaboration_provider(Arc<dyn CollaborationProvider>)`.
- `tools/list` appends Feishu schemas only when provider is configured.

- [ ] **Step 1: Write failing Bridge tests**

Prove:
- no provider => no Feishu tools in discovery;
- provider configured => exact v1 Feishu tool names appear;
- read tool with only write capability is rejected;
- write tool with only read capability is rejected;
- arbitrary `feishu_raw` or unknown name is rejected.

- [ ] **Step 2: Verify RED**

Run:
```bash
cargo test --manifest-path crates/local-tool-bridge/Cargo.toml --test feishu_integration
```

- [ ] **Step 3: Add capabilities and optional provider wiring**

Keep existing Files/Shell/Computer/Host routing unchanged. Feishu tools are handled locally by the provider seam rather than forwarded to CatDesk.

- [ ] **Step 4: Verify GREEN and run security-core tests**

```bash
cargo test --manifest-path crates/local-tool-bridge/Cargo.toml --test feishu_integration
cargo test --manifest-path crates/bridge-security-core/Cargo.toml
```

### Task 4: Base bounded writes with schema preflight

**Files:**
- Modify: `crates/collaboration-adapter/src/feishu.rs`
- Modify: `crates/collaboration-adapter/tests/feishu_contract.rs`
- Modify: `crates/local-tool-bridge/tests/feishu_integration.rs`

**Interfaces:**
- `BaseCreateRecord { base_token, table_id, fields }`
- `BaseUpdateRecord { base_token, table_id, record_id, fields }`

- [ ] **Step 1: Write failing tests**

Prove each write:
1. first issues a field-schema read;
2. does not dispatch write if schema read fails;
3. maps create/update to fixed Base commands;
4. rejects empty/non-object fields;
5. classifies a timeout after spawn as `result_unknown_after_dispatch`;
6. is never automatically retried.

- [ ] **Step 2: Verify RED**

Run adapter tests.

- [ ] **Step 3: Implement schema-preflight + one-shot write**

Use official Base record create/patch or stable shortcut. Never build raw endpoint paths from model input.

- [ ] **Step 4: Verify GREEN**

Run adapter + Bridge Feishu tests.

### Task 5: IM send/reply bounded writes

**Files:**
- Modify: `crates/collaboration-adapter/src/feishu.rs`
- Modify: `crates/collaboration-adapter/tests/feishu_contract.rs`
- Modify: `crates/local-tool-bridge/tests/feishu_integration.rs`

**Interfaces:**
- `MessageSend { chat_id, text, identity }`
- `MessageReply { message_id, text, identity }`
- identity enum `User|Bot`; default bot for automated writes.

- [ ] **Step 1: Write failing mapping/validation tests**

Prove:
- only `user|bot` is accepted;
- text/chat/message IDs are bounded and non-empty;
- send uses `im +messages-send`;
- reply uses the official reply shortcut/API command chosen from current CLI help;
- no delete/member/admin commands are reachable.

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Implement minimal send/reply mappings**

Include an idempotency key only where the official CLI supports it and it is generated by LAB, not caller-controlled.

- [ ] **Step 4: Verify GREEN**

### Task 6: Production configuration and readiness

**Files:**
- Modify: `crates/local-tool-bridge/src/main.rs`
- Modify: `scripts/lab_doctor.py`
- Modify: `tests/test_lab_doctor.py` or the existing doctor test file that owns layer checks
- Modify: `TECHNICAL.md`
- Modify: `SPEC.md`

**Interfaces:**
- `LAB_FEISHU_CLI`
- optional `LAB_FEISHU_PROFILE`
- `LAB_FEISHU_TIMEOUT_MS`

- [ ] **Step 1: Write failing startup/doctor tests**

Prove:
- unset config => Feishu disabled, core LAB still starts;
- configured missing/non-executable CLI => startup/readiness reports Feishu degraded without silently using AI-Bridge;
- configured valid fake CLI => Feishu layer healthy;
- secrets/auth URLs are redacted.

- [ ] **Step 2: Verify RED**

- [ ] **Step 3: Implement configuration/readiness**

Do not make Feishu a mandatory dependency for Files/Shell/Computer Runtime.

- [ ] **Step 4: Verify GREEN**

### Task 7: Setup Release, pinned lark-cli release, and real-account E2E

**Files:**
- Modify: `scripts/production_connector.py`
- Modify: `tests/test_production_connector.py`
- Modify: `docs/migration-status-2026-09-18.md` (create if absent)
- No credential files committed.

**Interfaces:**
- Setup Release: `feishu_admin_enabled=true`, `ProviderAdmin`, four fixed admin tools, no ordinary Feishu business tools;
- installer: fixed `@larksuite/cli@1.0.96`, absolute npm executable, private LAB provider directories;
- auth begin: fixed minimal `base,im` domains with `--no-wait --json`;
- auth completion: opaque LAB `flow_id`; provider device code never enters model-visible tool arguments;
- Pinned Release: exact CLI path + inode + SHA-256 + version `1.0.96`, ordinary ten Feishu tools only.

- [x] **Step 1: Implement and verify the bounded Setup/Admin runtime**

Focused evidence:
- Adapter fixed installer/admin contracts PASS;
- Bridge local background setup job PASS;
- opaque auth begin/complete flow PASS;
- `ProviderAdmin` separated from `CollaborationRead/Write`.

- [x] **Step 2: Implement two-stage production release contract**

Setup and Pinned Feishu runtime are mutually exclusive. Setup adds only the four admin tools and `provider_admin`; pinned runtime adds only ordinary collaboration capabilities and the ten business tools.

- [ ] **Step 3: Deploy an immutable Setup Release**

Use the canonical production install/start gates. Do not fabricate a revision or bypass Git/release fences.

- [ ] **Step 4: Install and configure the official CLI through LAB**

Call `feishu_setup_start(action=install)` and poll it to a terminal success. Then run the fixed `config init --new --lang zh` setup flow and surface only safe HTTPS verification URLs.

- [ ] **Step 5: Complete minimal Feishu authorization**

Call `feishu_auth_begin`, let the user complete the official browser authorization, then complete the flow with the opaque LAB `flow_id`. Request only Base + IM domains in v1.

- [ ] **Step 6: Produce and deploy the Pinned Feishu Release**

Generate a new immutable release that pins the installed CLI's exact executable path, inode, SHA-256 and version `1.0.96`. The pinned release must disable setup/admin mode and expose the ten ordinary Feishu tools.

- [ ] **Step 7: Read-only real smoke**

Against user-selected accessible resources:
- list Base tables/fields/records;
- search/list one group chat and recent messages.

- [ ] **Step 8: Write real smoke**

Only on a harmless user-approved test target:
- create/update one test Base record;
- send one test group message (bot preferred).

- [ ] **Step 9: Full verification**

Run the normal Cargo/Python suites once the LAB command/bootstrap environment supports them, plus the focused Rust/Python gates already established. Acceptance requires zero relevant test failures plus real LAB-origin Feishu read/write evidence.
