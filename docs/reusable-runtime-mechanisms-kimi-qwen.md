# Reusable Runtime Mechanisms from Kimi Code and Qwen Code

> Checked: 2026-09-09. Research candidate only.

## Selection principle

Borrow narrowly into LAB provider-neutral contracts. Do not fork a whole provider runtime into LAB and do not inherit provider-specific model-service assumptions.

## P0 candidates

### Qwen: safe MCP disconnect replay
- Reconnect/replay only when server is trusted, workspace is trusted, and the tool explicitly declares idempotent/read-only semantics.
- Missing/conflicting annotations => result may be unknown; verify before retry.
- LAB fit: map this into Tool Plane retry policy and signed capability receipts. LAB authorization remains authoritative.

### Qwen: progressive MCP discovery
- UI/runtime does not need to block on a slow MCP server.
- LAB fit: provider adapters can become AVAILABLE independently; slow Provider/Tool discovery should not freeze unrelated READY work.

### Kimi: event-stream session model
- Persisted session wire/event stream supports recovery and replay.
- Server API exposes turn/tool/subagent/background/goal lifecycle events and cursor-based event replay.
- LAB fit: compare with Runtime receipts and assignment events; borrow event naming/stream cursor ideas without creating a second state authority.

### Kimi/Qwen: agent control protocols
- Kimi ACP supports session new/load/resume/list/fork/cancel plus MCP forwarding.
- Qwen qwen serve wraps ACP in HTTP+SSE with long-lived workspace runtimes.
- LAB fit: use these as optional executor adapter seams rather than browser-driving local coding agents.

## P1 candidates

### Qwen: Goal verifier and evidence archive
- Independent verifier checks Goal complete/blocked state.
- Goal evidence can be compacted/archived and restored.
- LAB fit: compare with Reviewer/acceptance receipts; potential improvement is evidence compaction that preserves source references while keeping Controller state canonical.

### Qwen: budget-aware handoff
- Goal writes a handoff before budget exhaustion.
- LAB fit: mandatory handoff artifact when provider capacity/budget forces executor rotation.

### Qwen: Agent Team shared task list
- Persistent teammates can communicate and share tasks.
- LAB fit: useful UX/runtime pattern, but LAB TASK_LEDGER/DAG remains authoritative; never create a second shared task authority.

### Qwen: shadow-Git checkpointing
- Snapshot before AI file mutation without interfering with user's normal Git repository.
- LAB fit: evaluate as optional local rollback evidence for risky Computer/Tool operations, not a replacement for real Git integration gates.

### Kimi: Goal follow-up queue
- Queue a next Goal that starts only after current Goal completion.
- LAB fit: possible UX for queued Goals, but Controller must still perform project-wide recompute and canonical readiness checks.

### Kimi: subagent settlement semantics
- Parent does not treat a subagent as complete until background tasks settle.
- LAB fit: reinforces terminal-receipt rule: subagent 'finished talking' is not terminal if owned jobs are still nonterminal.

## P2 candidates

### Plugin packaging
- Kimi plugins can bundle Skills + Agents + MCP.
- Qwen extensions/plugins can bundle skills, agents and MCP servers.
- LAB fit: future Provider Adapter/plugin packaging format; must preserve LAB permission manifests and commercial-support metadata.

### Reconnect-safe client streams
- Kimi WebSocket cursor replay and Qwen SSE Last-Event-ID style recovery are useful client transport patterns.
- LAB fit: UI reconnect and executor event consumption; do not confuse transport replay with exactly-once side-effect execution.

## Things not to copy blindly

- Provider-side YOLO/trust as an authorization boundary.
- Infinite retry or unbounded retry without LAB recovery budgets.
- Provider-specific session/task stores as a second LAB ledger.
- Consumer subscription keys inside autonomous commercial backends.
- Direct CatDesk exposure that bypasses LAB signed ingress.
- Anti-bot/browser automation techniques for consumer Web providers.

## License/service boundary

- MoonshotAI/kimi-code source: MIT.
- Qwen Code source: Apache-2.0.
- Reusing source/design is subject to license notices/obligations.
- Model-service terms are independent. Kimi Code subscription and Alibaba Coding Plan currently restrict non-interactive/commercial backend usage.

## Suggested formal review order after ChatGPT reference pass

1. MCP safe replay annotations.
2. Progressive MCP discovery.
3. Executor control adapters over Kimi ACP and qwen serve.
4. Evidence compaction + budget-aware handoff.
5. Event cursor replay for LAB UI/executor streams.
6. Plugin-packaged Provider Adapter contract.
