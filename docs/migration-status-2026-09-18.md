# LAB Feishu Integration — 2026-09-18 Migration Status

## Scope

This checkpoint covers the LAB-native Feishu capability path requested by the user:

- Feishu Base / 多维表格;
- Feishu IM / group chat;
- official `lark-cli` provider only;
- no AI-Bridge runtime dependency.

## Architecture frozen

The provider path is:

```text
ChatGPT / Agent
  -> Local Agent Bridge
  -> Collaboration Adapter
  -> official lark-cli
  -> Feishu Open Platform
     -> Base
     -> IM
```

Installation/authentication and ordinary Feishu usage are split into two mutually exclusive immutable production releases.

### Setup Release

- capability: `ProviderAdmin`;
- tools: `feishu_setup_start`, `feishu_setup_poll`, `feishu_auth_begin`, `feishu_auth_complete`;
- fixed package: `@larksuite/cli@1.0.96`;
- fixed npm installer class;
- private LAB provider HOME/TMP/config/npm-cache;
- cleared inherited environment;
- Base+IM minimal auth domains;
- device code retained inside LAB behind an opaque `flow_id`;
- no ordinary Base/IM business tools.

### Pinned Feishu Release

After setup, a new immutable release must pin the installed CLI by exact path, inode, SHA-256 and exact version `1.0.96`.

Only that release exposes the ten ordinary Feishu tools using separate `CollaborationRead` / `CollaborationWrite` capabilities.

Setup and pinned modes fail closed if mixed.

## Implemented code

### collaboration-adapter

Implemented:

- fixed Base/IM argv mapping;
- strict argument validation;
- Base field-schema preflight before writes;
- direct argv process execution, no shell interpolation;
- bounded output and timeout handling;
- write `ResultUnknownAfterDispatch` semantics with no automatic retry;
- isolated provider HOME/TMP/config and cleared inherited environment;
- pinned installer contract;
- fixed setup/config/auth admin commands.

Focused tests: **23/23 PASS**.

### Local Tool Bridge

Implemented:

- `CollaborationRead`;
- `CollaborationWrite`;
- `ProviderAdmin`;
- local Feishu execution independent of the CatDesk default backend;
- ten ordinary Feishu MCP tools;
- four ProviderAdmin setup/auth tools;
- local background setup jobs;
- safe setup polling without raw credential output;
- opaque auth flow storage;
- recursive status credential redaction.

Focused Feishu/Admin integration: **6/6 PASS**.

Bridge Feishu configuration tests: **4/4 PASS**.

### Doctor

Added independent `collaboration` layer with:

- `DISABLED`;
- `SETUP`;
- `PINNED`;
- `MIXED` -> BROKEN;
- separate authorization readiness.

Doctor suite: **22/22 PASS**.

### Production release contract

Implemented:

- optional pinned Feishu runtime dependency;
- exact path/inode/SHA-256/version verification;
- Setup Release `feishu_admin_enabled`;
- Setup/Pinned mutual exclusion;
- release-specific required tool surfaces;
- release-specific Bridge capabilities.

Verification:

- Python source compilation PASS;
- focused two-stage release contract probe PASS.

The full historical production connector Python suite is not counted as PASS in this session because the existing outer CatDesk command sandbox blocks legacy test access to `/Users` and `/tmp`.

## Execution-plane blocker still open

The existing CatDesk-backed `start_command` sandbox currently:

- denies `/dev/null`, which prevents normal Git CLI startup;
- rewrites HOME, hiding the normal Cargo registry cache;
- denies general outbound network;
- restricts `/Users` and `/tmp` access needed by parts of the historical test harness.

The new Feishu Provider/Admin execution path is intentionally implemented directly inside Local Tool Bridge so it does not depend on those CatDesk shell limitations after deployment.

Protected `discover_current_entry` / `run_command` still fail internally in the current Web invocation, so no protected command acceptance is claimed.

## Git integrity evidence

Read-only parsing of native Git loose objects established:

- current main HEAD before this Feishu commit: `06a63f34b958210fb2fbd5f3d6610f3d178244e8`;
- staged changes: 0;
- multiple pre-existing unrelated unstaged project-document changes exist.

The Feishu commit must therefore include only the explicit Feishu implementation whitelist and must preserve all unrelated working-tree changes outside the commit.

## Remaining gates

1. create the isolated Feishu implementation commit without absorbing unrelated worktree changes;
2. build/install an immutable Setup Release;
3. expose the four ProviderAdmin tools through the current LAB Connector;
4. install official `lark-cli@1.0.96`;
5. complete config + minimal Base/IM user authorization;
6. generate and deploy the Pinned Feishu Release;
7. run real Base read/create/update smoke;
8. run real group-chat search/read + one harmless test message;
9. run the normal full Cargo/Python regression suites after the LAB command/bootstrap execution environment is repaired.

No real Feishu account E2E has been claimed yet.
