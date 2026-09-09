# Provider-Neutral Web Model Executor Contract

> Status: research candidate only. Non-authoritative until accepted by the unique LAB Controller.
> Scope: Multi-Provider feasibility. This document does not modify ChatGPT reference implementation or Runtime lifecycle.

## 1. Design rule

LAB must not encode provider DOM, login flow, model names, usage-limit UI, or completion heuristics in Adaptive Agent Runtime.

The stable split is:

\`\`\`text
Adaptive Agent Runtime
  -> WebModelExecutor (provider-neutral lifecycle)
      -> ProviderAdapter (provider-specific capability implementation)
          -> Provider Web / official agent surface
              -> LAB Tool Plane
                  -> Files / Shell / Process / Jobs / Browser / Computer Runtime
\`\`\`

There is one LAB Tool Plane. A new Provider adds an Adapter, not a second Local Bridge.

## 2. What is already provider-neutral in the ChatGPT reference

The current \`experiments/web-chatgpt-executor/executor.mjs\` already contains a reusable lifecycle skeleton:

\`\`\`text
assignment_id
  -> execution_id
  -> attempt
  -> provider/model request facts
  -> browser/session identity
  -> conversation identity
  -> queued / running / completed / failed
  -> result locator
  -> terminal receipt
\`\`\`

Reusable concepts:
- durable execution record;
- append-only execution event journal;
- prompt hash/length instead of raw prompt in execution metadata;
- assignment/execution/conversation correlation;
- terminal result hash and length;
- explicit failed terminal;
- recovery generation;
- observed-vs-requested model facts.

These concepts should move behind a provider-neutral executor contract only when the ChatGPT reference mainline is ready for that refactor. This research task does not perform that refactor.

## 3. ChatGPT-specific coupling that belongs in ChatGPTAdapter

Current coupling is concentrated in the browser/UI adapter:
- \`selectPersonalChatMode\`;
- ChatGPT model/power menu discovery;
- ChatGPT reasoning-effort slider semantics;
- \`chatgpt-web-mcp\` and \`ChatGPTBrowser\`;
- ChatGPT-specific provider strings;
- ChatGPT-specific mode/error messages;
- ChatGPT completion/conversation extraction;
- ChatGPT Host attestation origin and conversation routing.

The provider-neutral Runtime must not know these details.

## 4. Stable Runtime-facing lifecycle

A Provider implementation advertises capabilities. Runtime calls only operations that the Adapter declares supported.

Recommended conceptual interface:

\`\`\`ts
type ProviderId = string;

type CapacityState =
  | "AVAILABLE"
  | "THROTTLED"
  | "LIMIT_REACHED"
  | "UNKNOWN";

type AdapterSupportClass =
  | "OFFICIAL_CONNECTOR"
  | "PROVIDER_SUPPORTED_EXECUTOR"
  | "BROWSER_ADAPTER"
  | "EXPERIMENTAL";

interface WebModelExecutor {
  spawn(assignment): Promise<ExecutionHandle>;
  send(executionId, assignment): Promise<DispatchReceipt>;
  status(executionId): Promise<ExecutionStatus>;
  result(executionId): Promise<ExecutionResult>;
  identity(executionId): Promise<ExecutionIdentity>;

  // Optional. Must be capability-advertised, never assumed.
  cancel?(executionId): Promise<CancelReceipt>;
  resume?(executionId): Promise<ResumeReceipt>;
  capacity?(): Promise<ProviderCapacity>;
}

interface ProviderAdapter {
  providerId: ProviderId;
  supportClass(): AdapterSupportClass;
  capabilities(): ProviderCapabilities;

  ensureSession(): Promise<SessionFacts>;
  createOrBindExecution(input): Promise<ProviderExecution>;
  submit(input): Promise<DispatchReceipt>;
  observe(input): Promise<Observation>;
  extractResult(input): Promise<ExecutionResult>;
  identity(input): Promise<ExecutionIdentity>;
  classifyCapacity(input): Promise<ProviderCapacity>;

  // Optional capabilities:
  selectModel?(input): Promise<ModelFacts>;
  selectReasoningMode?(input): Promise<ReasoningFacts>;
  cancel?(input): Promise<CancelReceipt>;
  recover?(input): Promise<RecoveryReceipt>;
}
\`\`\`

## 5. Capability rules

An Adapter must not fake parity with another Provider.

Examples:
- if a Provider has no reliable cancel semantic, advertise \`cancel=false\`;
- if model selection is not observable, report \`model_attestation=unavailable\`;
- if only an official MCP inbound path exists, do not claim LAB->Web executor support;
- if an official agent/CLI surface exists but consumer Web automation is prohibited, represent them as separate surfaces.

## 6. Identity and evidence

Runtime consumes a provider-neutral identity envelope, but evidence is provider-specific:

\`\`\`text
provider_id
execution_id
provider_surface
provider_session_id?          # if safely observable
conversation_id?              # if provider exposes a stable identity
target_id?                    # browser/host specific
origin
identity_attestation_level
receipt_id
observed_at
\`\`\`

Rules:
- prompt claims are never identity;
- active-tab fallback is never identity;
- provider DOM/title alone is correlation, not strong attestation;
- each ProviderAdapter owns its own machine-verifiable origin rules;
- Runtime validates the normalized envelope and receipt binding, not provider DOM.

Do not generalize ChatGPT Host attestation by replacing \`chatgpt.com\` with a provider string. Each provider needs its own origin/session/target evidence contract.

## 7. Capacity-aware routing contract

Only expose facts the Adapter can reliably observe:

\`\`\`ts
interface ProviderCapacity {
  state: "AVAILABLE" | "THROTTLED" | "LIMIT_REACHED" | "UNKNOWN";
  observedAt: string;
  resetsAt?: string;          // only if provider gives a reliable reset
  modelScope?: string;
  concurrencyHint?: number;   // only if measured/documented
  evidence: "provider_ui" | "provider_api" | "provider_docs" | "runtime_observed";
}
\`\`\`

Never invent “remaining 42%” from retry behavior. If remaining allowance is not machine-readable, use \`UNKNOWN\`.

## 8. Routing inputs

Future route policy may consider:
- task suitability;
- quality;
- latency;
- explicit monetary cost;
- provider availability;
- observed capacity state;
- concurrency;
- reviewer independence;
- commercial support class.

A rate/usage limit is a routing constraint, not something LAB may bypass.

## 9. Provider-native agent capability does not replace LAB

A Provider may already have browser/agent features (for example a Web-browsing agent) without having LAB-equivalent local execution. Keep these capability planes separate:

```text
Provider-native Web Agent
  = web navigation / provider-owned connected apps / provider-owned agent actions

LAB Tool Plane
  = local Files / Shell / Process / Jobs / Browser / Computer Runtime
    + cross-provider dispatch / recovery / project orchestration
```

Provider-native Web agents are useful upstream execution surfaces. LAB's value is to extend them into controlled local-machine execution and multi-provider orchestration. Do not mark a Provider as having `LOCAL_EXECUTION` merely because it can automate browser tabs or provider-connected SaaS apps.

## 9. Commercial support is outside technical Adapter success

Every surface carries two independent verdicts:
- \`TECHNICAL_FEASIBILITY\`;
- \`COMMERCIAL_SUPPORTABILITY\`.

A browser PoC can be technically GREEN while the commercial gate remains NO-GO.

## 10. Trigger for implementation

Before \`CHATGPT_LAB_REFERENCE_PASS\`:
- freeze this contract;
- maintain matrix/source audit;
- allow only low-cost GO/NO-GO spikes.

After \`CHATGPT_LAB_REFERENCE_PASS\` and core M0 stability:
- Controller may promote \`MULTI_PROVIDER_POC\`;
- fresh-read matrix;
- select one Provider/surface;
- run minimal A and/or B PoC;
- only then consider a formal Adapter.
