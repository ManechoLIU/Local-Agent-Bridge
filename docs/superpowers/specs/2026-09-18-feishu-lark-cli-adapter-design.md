# Feishu via LAB / lark-cli Adapter Design

> Date: 2026-09-18
> Status: approved direction from current conversation; implementation baseline
> Scope: first-party LAB integration with official `lark-cli` for Feishu Base (Bitable) and IM/group chat

## 1. Problem

The user needs Feishu capabilities inside the LAB product path, specifically:

- Base / 多维表格: inspect schema, list/search records, create/update records;
- IM / 群聊: search/list chats, read recent messages, send messages and replies.

This must not depend on AI-Bridge. LAB remains the primary execution path.

## 2. Architectural decision

Use the official `lark-cli` as the Feishu provider implementation and expose a constrained, stable LAB tool surface above it.

```text
ChatGPT / Agent
      ↓
Local Agent Bridge
      ↓
External Collaboration Adapter seam
      ↓
Feishu Adapter
      ↓
official lark-cli
      ↓
Feishu Open Platform
   ├─ Base / Bitable
   └─ IM / Group Chat
```

LAB does not reimplement Feishu Open APIs. Provider-specific authentication, token refresh and endpoint details remain owned by `lark-cli`.

The adapter seam is provider-neutral enough for future Notion/Google/Jira-style integrations, but v1 implements only Feishu. No new generic SaaS platform, daemon, database or event bus is introduced.

## 3. Product/source-of-truth boundary

Feishu is an external collaboration surface, not a second project truth source.

- Product truth remains `SPEC.md / DESIGN.md / TECHNICAL.md`.
- Runtime execution truth remains `TASK_LEDGER.md` plus machine state.
- Base tables may mirror or operationalize approved planning/execution data, but cannot silently override canonical project documents.
- Inbound Feishu data that would change product intent must enter through the existing planning/change-request path.

Ordinary Feishu tool usage can bypass Product Planning and Adaptive Agent Runtime, just like ordinary LAB Tool Plane usage.

## 4. Security and identity

### 4.1 Authentication

- LAB never asks the model/user to paste Feishu access tokens.
- `lark-cli` owns OAuth/app credential storage using its supported mechanisms.
- LAB invokes a configured `lark-cli` binary/profile; secrets are not placed in MCP tool arguments, logs or model-visible output.
- Initial interactive authorization remains a user action in the official browser flow.

### 4.2 LAB capabilities

Add two explicit Bridge capabilities:

- `CollaborationRead`: status/schema/list/search/read operations.
- `CollaborationWrite`: Base create/update and IM send/reply operations.

Write capability never implicitly follows from read capability.

### 4.3 Command execution

The Feishu adapter MUST:

- invoke `lark-cli` directly with argv arrays, never through a shell;
- allow only a fixed command mapping for exposed LAB tools;
- reject arbitrary service/method/path/flag injection;
- apply bounded timeouts and output-size limits;
- require JSON output where supported and parse it before returning;
- preserve non-zero exit status and structured provider errors;
- avoid automatic write retries after an uncertain post-dispatch result.

## 5. Stable LAB tool surface (v1)

Read tools:

1. `feishu_status`
   - reports CLI availability, version, effective identity/profile and auth readiness;
   - must not return secret token material.

2. `feishu_base_list_tables`
   - args: `base_token`.

3. `feishu_base_list_fields`
   - args: `base_token`, `table_id`.

4. `feishu_base_list_records`
   - args: `base_token`, `table_id`, optional `view_id`, `page_size`, `page_token`.
   - no arbitrary raw filter expression in v1.

5. `feishu_chat_search`
   - args: `query`, optional `page_size`.

6. `feishu_message_list`
   - args: `chat_id`, optional `page_size`, `page_token`.

Write tools:

7. `feishu_base_create_record`
   - args: `base_token`, `table_id`, `fields` JSON object.
   - adapter performs a field-schema read before dispatch or rejects if schema cannot be obtained.

8. `feishu_base_update_record`
   - args: `base_token`, `table_id`, `record_id`, `fields` JSON object.
   - same schema preflight requirement.

9. `feishu_message_send`
   - args: `chat_id`, `text`, optional `identity` = `user|bot`.
   - default identity: `bot` for automated sends.

10. `feishu_message_reply`
    - args: `message_id`, `text`, optional `identity` = `user|bot`.
    - default identity: `bot`.

Delete operations, group membership mutation, Base schema mutation, raw API escape hatches, attachment upload/download and event/webhook subscriptions are explicitly out of v1.

## 6. Provider mapping

Use official CLI commands/shortcuts where stable, with raw OpenAPI calls excluded from the LAB tool surface.

Base mapping uses the `base` service. Official guidance requires reading field schema before record writes; the adapter enforces this invariant.

IM mapping uses the `im` service for chat search/list and message send/reply.

The provider wrapper owns command-version compatibility. LAB tool names and argument schemas do not track CLI command spelling one-for-one.

## 7. Process/layout

Introduce a focused provider module rather than embedding Feishu logic into the large Bridge ingress file:

```text
crates/collaboration-adapter/
  Cargo.toml
  src/lib.rs
  src/feishu.rs
  tests/feishu_contract.rs
```

`collaboration-adapter` is a library crate. It contains:

- provider-neutral request/result/error types;
- `CollaborationProvider` trait;
- `LarkCliProvider` implementation;
- bounded process runner abstraction for test injection.

`local-tool-bridge` depends on this crate and owns:

- capability enforcement;
- MCP tool schemas;
- routing of `feishu_*` calls;
- merging Feishu tools into `tools/list`;
- translation between MCP arguments/results and provider requests.

No separate daemon is required in v1.

## 8. Error/recovery contract

Errors are classified at least as:

- `cli_unavailable`
- `auth_required`
- `permission_denied`
- `invalid_argument`
- `provider_rate_limited`
- `provider_rejected`
- `timeout_before_dispatch`
- `result_unknown_after_dispatch`
- `invalid_provider_output`

Read operations may be retried only when the provider classifies the failure as pre-dispatch/transient.

Write operations MUST NOT automatically retry `result_unknown_after_dispatch`.

## 9. Observability

Each call returns non-secret metadata:

- provider = `feishu`;
- backend = `lark-cli`;
- operation;
- effective identity = `user|bot` when observable;
- provider request/result status;
- duration;
- result provenance.

Logs redact tokens, authorization URLs and credential material.

## 10. Configuration

Required runtime configuration:

- `LAB_FEISHU_CLI`: absolute path to `lark-cli` binary.
- optional `LAB_FEISHU_PROFILE`: dedicated CLI profile name.
- `LAB_FEISHU_TIMEOUT_MS`: bounded provider timeout.

Production refuses to expose Feishu tools if the configured CLI path is missing, not executable or resolves through a symlink to an untrusted location.

## 11. Thin slices and acceptance

### Slice A — provider seam + read-only Feishu

Acceptance:

- fake CLI fixture proves argv allowlisting, timeout/output bounds and JSON parsing;
- `feishu_status`, Base list/schema/records and chat/message reads appear in `tools/list`;
- read calls require `CollaborationRead`;
- secrets are absent from MCP responses/logs.

### Slice B — bounded writes

Acceptance:

- Base create/update requires `CollaborationWrite` and schema preflight;
- IM send/reply requires `CollaborationWrite`;
- no arbitrary command injection;
- uncertain post-dispatch writes return `result_unknown_after_dispatch` and are not retried.

### Slice C — real account E2E

Acceptance:

- official `lark-cli` installed and authenticated through user-completed browser authorization;
- real Base read + harmless test-record create/update on a user-selected test table;
- real group/chat search + read and one user-approved test message;
- all calls originate through LAB, not AI-Bridge.


## 12. Production bootstrap and immutable provider releases

Feishu installation/authentication and ordinary Base/IM usage use two mutually exclusive production release modes.

### Setup Release

The Setup Release exists only to bootstrap the official CLI and authorization without granting a generic networked shell.

- capability: `ProviderAdmin`;
- tools: `feishu_setup_start`, `feishu_setup_poll`, `feishu_auth_begin`, `feishu_auth_complete`;
- fixed package: `@larksuite/cli@1.0.96`;
- fixed installer executable class: absolute path whose basename is `npm`;
- fixed private install root under `~/.local-agent-bridge/providers/feishu/npm`;
- fixed private `HOME`, `TMPDIR`, `LARKSUITE_CLI_CONFIG_DIR`, and npm cache;
- inherited process environment is cleared;
- config action is fixed to `config init --new --lang zh`;
- auth begins with the minimal `base,im` domain set and a non-blocking JSON flow;
- the provider `device_code` is retained inside LAB and exposed to the model only as an opaque `flow_id`;
- setup job polling returns state and HTTPS verification URLs, not raw credential-bearing stdout/stderr.

The Setup Release MUST NOT expose ordinary Feishu Base/IM business tools and MUST NOT contain a pinned `feishu_cli` runtime dependency.

### Pinned Feishu Release

After setup succeeds, the next immutable release pins the installed CLI using:

- exact executable path;
- inode;
- SHA-256;
- exact semantic version `1.0.96`.

Only this release enables `CollaborationRead` / `CollaborationWrite` and the ten ordinary Feishu tools. `ProviderAdmin` is not required in this mode.

Setup mode and pinned runtime mode are mutually exclusive and fail closed if mixed. This preserves the existing LAB immutable-release contract instead of executing a mutable provider binary outside release validation.

### Diagnostics

`lab_doctor` exposes Feishu as an independent `collaboration` layer:

- `DISABLED` — no Feishu surface;
- `SETUP` — the four admin tools are exposed, business tools are not ready;
- `PINNED` — the ten Base/IM tools are exposed from a pinned runtime;
- `MIXED` — setup and business tools coexist and the layer is `BROKEN`.

Authorization readiness remains distinct from Bridge/tunnel health.

## 13. Current implementation evidence

The implementation baseline has the following focused evidence before real account E2E:

- collaboration adapter: 23/23 focused tests PASS;
- Bridge Feishu/Admin integration: 6/6 PASS;
- Bridge Feishu configuration tests: 4/4 PASS;
- layered doctor: 22/22 PASS;
- production connector Python files pass syntax compilation;
- two-stage Setup/Pinned release contract passes a focused pure-logic probe.

These are code-level gates only. They do not claim that `lark-cli` is already installed, that the user's Feishu account is authorized, or that Base/group-chat real E2E is complete.

## 14. Non-goals

- Reimplement Feishu APIs/SDK.
- Depend on AI-Bridge.
- Make Feishu Base canonical project storage.
- Expose arbitrary `lark-cli api` or arbitrary shell.
- Add deletions or membership management in v1.
- Add webhook/event ingestion in v1.
