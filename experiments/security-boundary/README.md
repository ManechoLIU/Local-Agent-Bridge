# LAB-SAFE-01 security boundary harness

This experiment turns the known CatDesk safety findings into executable contracts before they are promoted into the Local Agent Bridge core.

## Security contract under test

A local request is authorized against four distinct values: `principal_id`, `execution_session_id`, `capability_id`, and `workspace_id`. A capability grant must match all four and must explicitly contain the requested capability. None of these values is a Runtime Controller identity or a ChatGPT conversation identity.

Filesystem operations use directory-file-descriptor traversal on Unix. Parent components are opened with `openat(..., O_DIRECTORY | O_NOFOLLOW)`. File reads/writes use `O_NOFOLLOW`, inspect the opened inode with `fstat`, and reject regular files with more than one hard link. New targets are created with `O_CREAT | O_EXCL`. Moves use parent directory descriptors plus `fstatat(..., AT_SYMLINK_NOFOLLOW)` and `renameat`; cross-device fallback currently fails closed instead of copying across an unverified boundary.

The macOS shell prototype uses `/usr/bin/sandbox-exec` as a **PoC isolation backend**, not as a long-term cross-platform product dependency. The profile permits process execution and normal system runtime reads, exposes the selected workspace, blocks user-data roots outside that workspace, blocks writes outside the workspace, does not allow network access, and launches with a cleared environment containing only a small explicit allowlist (`HOME`, `TMPDIR/TMP/TEMP`, `PATH`, `LANG`).

## Current executable gates

`cargo test --manifest-path experiments/security-boundary/Cargo.toml` currently covers 19 passing tests, including:

- capability grant binding across principal/session/capability/workspace;
- signed local-ingress request proofs that bind capability/principal/session/workspace + request id/nonce + MCP method/tool + body hash with HMAC-SHA256;
- capability issuance TTL, revocation, session-generation fencing and replay rejection;
- generation-fenced master-key rotation with crash-safe key-first/state-pointer commit ordering;
- private 0600 authority key/state persistence with replay + revocation surviving restart;
- signed ingress header serialization without exporting the per-capability signing secret;
- explicit proof that `x-openai-subject` / `x-openai-session`-style correlation facts remain diagnostic and cannot authorize by themselves;
- hardlink read escape rejection;
- hardlink write escape rejection;
- existing symlink write rejection without modifying its target;
- nonexistent write below a symlink parent rejection;
- nonexistent move destination below a symlink parent rejection;
- traversal rejection;
- normal in-workspace write/read/move;
- macOS shell workspace read/write success;
- macOS shell read denial for sensitive paths outside the workspace;
- macOS shell outbound network denial;
- inherited environment secret removal.

## CatDesk candidate

A separate CatDesk worktree is used as the replace/patch candidate:

`/Users/echoman/CatDesk-PoC-20260907/worktrees/lab-safe-01`

The security RED tests there first reproduced the current upstream behavior. After the candidate patch, the security tests pass using Unix directory-FD workspace operations plus a macOS sandbox helper. CatDesk's unrelated `change_tracking` test failures are present on the unchanged upstream baseline and are therefore not attributed to this candidate.

## Remaining limitations before product promotion

- Capability issuance/TTL/revocation/session-generation/replay now have a working local authority with private restart persistence; production ingress persists request IDs before backend dispatch, and master-key rotation is generation-fenced with versioned keys. The v1 same-UID threat boundary is now explicitly scoped; crash/replay recovery and generation-fenced key rotation have real-process coverage. Remaining work is independent post-fix security review and packaging.
- OpenAI tunnel correlation headers are not promoted to authenticated Web conversation or Runtime Controller identity.
- `sandbox-exec` is macOS-specific and treated as a replaceable backend. Linux already has CatDesk Landlock support; Windows still needs a product-grade filesystem/network isolation decision.
- The Unix inode checks substantially reduce symlink/hardlink/TOCTOU risk, but a malicious concurrent same-UID process can still race hard-link creation around an already-open file. A stronger worker/process ownership boundary is required if same-UID hostile concurrency is in scope.
- Browser and Computer Runtime capabilities remain separate authorization domains and are not covered by this harness.
