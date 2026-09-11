import { createHash, createHmac, randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import {
  chmodSync, closeSync, existsSync, fsyncSync, lstatSync, mkdirSync, openSync,
  readFileSync, renameSync, unlinkSync, writeFileSync
} from "node:fs";
import { dirname, join } from "node:path";

export const ATTESTATION_PROVENANCE = "browser_host_machine_attested_v1";
export const MACHINE_OBSERVATION_SOURCE = "browser_cdp_page_frame_tree_v1";
export const VERIFIED_TARGET_PROVENANCE = "runtime_host_verifier_v1";
export const REENTRY_RECEIPT_PROVENANCE = "browser_host_reentry_receipt_v1";
export const INVOCATION_ORIGIN_PROVENANCE = "runtime_invocation_origin_v1";
export const MACHINE_INVOCATION_ORIGIN_SOURCE = "browser_cdp_invocation_origin_v1";
export const CURRENT_ENTRY_PROVENANCE = "runtime_host_current_entry_v1";
export const CURRENT_ENTRY_SCOPE = "runtime_invocation";
export const HOST_INVOCATION_CONTEXT_SOURCE = "host_invocation_context_v1";

function nonEmpty(value, name) {
  const text = String(value ?? "").trim();
  if (!text) throw new Error(`${name} is required`);
  return text;
}

function positiveInteger(value, name) {
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < 1) throw new Error(`${name} must be a positive integer`);
  return number;
}

function nowMs(clock) {
  return Number(clock());
}

function sha256Text(value) {
  return createHash("sha256").update(String(value)).digest("hex");
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function hmac(secret, value) {
  return createHmac("sha256", secret).update(canonicalJson(value)).digest("hex");
}

function normalizedVerifiedTarget(input) {
  const target = {
    schema_version: Number(input?.schema_version),
    verified_target_id: nonEmpty(input?.verified_target_id, "verified_target_id"),
    host_type: nonEmpty(input?.host_type, "verified_target.host_type"),
    conversation_id: nonEmpty(input?.conversation_id, "verified_target.conversation_id"),
    browser_target_id: nonEmpty(input?.browser_target_id, "verified_target.browser_target_id"),
    top_frame_id: nonEmpty(input?.top_frame_id, "verified_target.top_frame_id"),
    loader_id: nonEmpty(input?.loader_id, "verified_target.loader_id"),
    secure_origin: nonEmpty(input?.secure_origin, "verified_target.secure_origin"),
    target_generation: positiveInteger(input?.target_generation, "verified_target.target_generation"),
    ownership_generation: positiveInteger(input?.ownership_generation, "verified_target.ownership_generation"),
    host_receipt_id: nonEmpty(input?.host_receipt_id, "verified_target.host_receipt_id"),
    verified_at_ms: positiveInteger(input?.verified_at_ms, "verified_target.verified_at_ms"),
    provenance: nonEmpty(input?.provenance, "verified_target.provenance")
  };
  if (target.schema_version !== 1) throw new Error("verified target schema_version is unsupported");
  if (target.host_type !== "chatgpt_web") throw new Error("verified target host_type is unsupported");
  if (target.secure_origin !== "https://chatgpt.com") throw new Error("verified target secure origin is invalid");
  if (target.provenance !== VERIFIED_TARGET_PROVENANCE) throw new Error("re-entry requires a verified target from Runtime Host verifier");
  return target;
}

function signVerifiedTarget(secret, input) {
  const target = normalizedVerifiedTarget(input);
  return { ...target, verifier_mac_sha256: hmac(secret, target) };
}

function authenticateVerifiedTarget(secret, input) {
  const target = normalizedVerifiedTarget(input);
  const rawMac = input?.verifier_mac_sha256;
  if (typeof rawMac !== "string" || !rawMac.trim()) {
    throw new Error("verified target authentication failed");
  }
  const mac = rawMac.trim();
  if (!secureEqualHex(mac, hmac(secret, target))) throw new Error("verified target authentication failed");
  return { ...target, verifier_mac_sha256: mac };
}

function optionalDiagnosticText(value) {
  if (value == null) return null;
  const text = String(value).trim();
  return text ? text.slice(0, 256) : null;
}

function sanitizeSubmitDiagnostics(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) return null;
  const debuggerAttach = input.debugger_attach && typeof input.debugger_attach === "object" ? input.debugger_attach : {};
  const composer = input.composer_preparation && typeof input.composer_preparation === "object" ? input.composer_preparation : {};
  const sendControl = input.send_control_resolution && typeof input.send_control_resolution === "object" ? input.send_control_resolution : {};
  const trusted = input.trusted_dispatch && typeof input.trusted_dispatch === "object" ? input.trusted_dispatch : {};
  const confirmation = input.confirmation && typeof input.confirmation === "object" ? input.confirmation : {};
  const foreground = input.foreground && typeof input.foreground === "object" ? input.foreground : {};
  return {
    schema_version: 1,
    wake_id: optionalDiagnosticText(input.wake_id),
    browser_target_id: optionalDiagnosticText(input.browser_target_id),
    phase: optionalDiagnosticText(input.phase),
    debugger_attach: { attempted: debuggerAttach.attempted === true, ok: debuggerAttach.ok === true },
    composer_preparation: {
      attempted: composer.attempted === true,
      ok: composer.ok === true,
      status: optionalDiagnosticText(composer.status),
      rollback_attempted: composer.rollback_attempted === true,
      rollback_succeeded: composer.rollback_succeeded === true
    },
    send_control_resolution: {
      attempted: sendControl.attempted === true,
      ok: sendControl.ok === true,
      selector_kind: optionalDiagnosticText(sendControl.selector_kind),
      coordinates_changed_since_prepare: sendControl.coordinates_changed_since_prepare === true
    },
    trusted_dispatch: {
      attempted: trusted.attempted === true,
      focus_emulation: trusted.focus_emulation === true,
      mouse_moved: trusted.mouse_moved === true,
      mouse_pressed: trusted.mouse_pressed === true,
      mouse_released: trusted.mouse_released === true
    },
    confirmation: {
      attempted: confirmation.attempted === true,
      ok: confirmation.ok === true,
      composer_empty: typeof confirmation.composer_empty === "boolean" ? confirmation.composer_empty : null,
      exact_new_user_message: typeof confirmation.exact_new_user_message === "boolean" ? confirmation.exact_new_user_message : null
    },
    foreground: {
      mode: optionalDiagnosticText(foreground.mode),
      activated_target: foreground.activated_target === true,
      restored_previous_active: foreground.restored_previous_active === true,
      user_changed_focus: foreground.user_changed_focus === true
    },
    failure_stage: optionalDiagnosticText(input.failure_stage),
    dispatch_attempted: input.dispatch_attempted === true,
    submit_confirmed: input.submit_confirmed === true,
    retryable: input.retryable === true
  };
}

function secureEqualHex(left, right) {
  try {
    const a = Buffer.from(String(left), "hex");
    const b = Buffer.from(String(right), "hex");
    return a.length === b.length && a.length > 0 && timingSafeEqual(a, b);
  } catch {
    return false;
  }
}

export function conversationIdFromChatgptRoute(urlValue) {
  const url = new URL(String(urlValue));
  if (url.protocol !== "https:" || url.hostname !== "chatgpt.com") {
    throw new Error("ChatGPT machine observation must use https://chatgpt.com");
  }
  const parts = url.pathname.split("/").filter(Boolean);
  const cIndex = parts.lastIndexOf("c");
  if (cIndex < 0 || !parts[cIndex + 1]) throw new Error("current ChatGPT document has no conversation route");
  const conversationId = parts[cIndex + 1];
  if (!/^[0-9a-f-]{20,80}$/i.test(conversationId)) throw new Error("conversation route is not a stable conversation id");
  return conversationId;
}

export function machineObservationFromFrameTree({ tab_id, frameTree, observed_at_ms }) {
  const targetId = nonEmpty(tab_id, "tab_id");
  const top = frameTree?.frameTree?.frame ?? frameTree?.frame ?? frameTree;
  if (!top || typeof top !== "object") throw new Error("top-level frame machine record is required");
  const origin = nonEmpty(top.securityOrigin, "securityOrigin");
  if (origin !== "https://chatgpt.com") throw new Error("top-level frame is not the secure ChatGPT origin");
  const conversationId = conversationIdFromChatgptRoute(nonEmpty(top.url, "top frame url"));
  return validateMachineObservation({
    host_type: "chatgpt_web",
    browser_target_id: targetId,
    top_frame_id: nonEmpty(top.id, "top frame id"),
    loader_id: nonEmpty(top.loaderId, "loader id"),
    secure_origin: origin,
    conversation_id: conversationId,
    observed_at_ms: positiveInteger(observed_at_ms, "observed_at_ms"),
    source: MACHINE_OBSERVATION_SOURCE
  });
}

export function validateMachineObservation(input) {
  const observation = {
    host_type: nonEmpty(input?.host_type, "host_type"),
    browser_target_id: nonEmpty(input?.browser_target_id, "browser_target_id"),
    top_frame_id: nonEmpty(input?.top_frame_id, "top_frame_id"),
    loader_id: nonEmpty(input?.loader_id, "loader_id"),
    secure_origin: nonEmpty(input?.secure_origin, "secure_origin"),
    conversation_id: nonEmpty(input?.conversation_id, "conversation_id"),
    observed_at_ms: positiveInteger(input?.observed_at_ms, "observed_at_ms"),
    source: nonEmpty(input?.source, "source")
  };
  if (observation.host_type !== "chatgpt_web") throw new Error("unsupported host_type");
  if (observation.secure_origin !== "https://chatgpt.com") throw new Error("ChatGPT host attestation requires secure chatgpt.com origin");
  if (observation.source !== MACHINE_OBSERVATION_SOURCE) {
    throw new Error("Host attestation requires browser machine document provenance; URL/DOM/UI observation is not accepted");
  }
  if (!/^[0-9a-f-]{20,80}$/i.test(observation.conversation_id)) {
    throw new Error("conversation_id is not a stable ChatGPT conversation identifier");
  }
  return observation;
}


export function validateMachineInvocationOrigin(input) {
  const origin = {
    host_type: nonEmpty(input?.host_type, "invocation_origin.host_type"),
    invocation_id: nonEmpty(input?.invocation_id, "invocation_origin.invocation_id"),
    operation: nonEmpty(input?.operation, "invocation_origin.operation"),
    browser_target_id: nonEmpty(input?.browser_target_id, "invocation_origin.browser_target_id"),
    top_frame_id: nonEmpty(input?.top_frame_id, "invocation_origin.top_frame_id"),
    loader_id: nonEmpty(input?.loader_id, "invocation_origin.loader_id"),
    secure_origin: nonEmpty(input?.secure_origin, "invocation_origin.secure_origin"),
    conversation_id: nonEmpty(input?.conversation_id, "invocation_origin.conversation_id"),
    observed_at_ms: positiveInteger(input?.observed_at_ms, "invocation_origin.observed_at_ms"),
    source: nonEmpty(input?.source, "invocation_origin.source")
  };
  if (origin.host_type !== "chatgpt_web") throw new Error("unsupported invocation origin host_type");
  if (origin.secure_origin !== "https://chatgpt.com") throw new Error("invocation origin requires secure chatgpt.com origin");
  if (origin.source !== MACHINE_INVOCATION_ORIGIN_SOURCE) {
    throw new Error("invocation origin requires browser machine invocation provenance");
  }
  if (!/^[0-9a-f-]{20,80}$/i.test(origin.conversation_id)) {
    throw new Error("invocation origin conversation_id is not a stable ChatGPT conversation identifier");
  }
  return Object.freeze(origin);
}


export function validateHostInvocationContext(input) {
  const context = {
    entry_scope: nonEmpty(input?.entry_scope, "current_entry.entry_scope"),
    machine_source: nonEmpty(input?.machine_source, "current_entry.machine_source"),
    conversation_id: nonEmpty(input?.conversation_id, "current_entry.conversation_id"),
    browser_target_id: nonEmpty(input?.browser_target_id, "current_entry.browser_target_id"),
    top_frame_id: nonEmpty(input?.top_frame_id, "current_entry.top_frame_id"),
    loader_id: nonEmpty(input?.loader_id, "current_entry.loader_id"),
    secure_origin: nonEmpty(input?.secure_origin, "current_entry.secure_origin"),
    generation_anchor_sha256: digest(input?.generation_anchor_sha256, "current_entry.generation_anchor_sha256"),
    observed_at_ms: positiveInteger(input?.observed_at_ms, "current_entry.observed_at_ms")
  };
  if (context.entry_scope !== CURRENT_ENTRY_SCOPE) {
    throw new Error("current-entry evidence must be bound to this Runtime invocation");
  }
  if (context.machine_source !== HOST_INVOCATION_CONTEXT_SOURCE) {
    throw new Error("current-entry evidence requires trusted Host invocation context");
  }
  if (context.secure_origin !== "https://chatgpt.com") {
    throw new Error("current-entry evidence requires secure ChatGPT origin");
  }
  if (!/^[0-9a-f-]{20,80}$/i.test(context.conversation_id)) {
    throw new Error("current-entry conversation_id is not a stable ChatGPT conversation identifier");
  }
  return Object.freeze(context);
}

export function signCurrentEntryEvidence({
  secret,
  machineContext,
  targetGeneration,
  ownershipGeneration,
  clock = Date.now,
  ttlMs = 15_000,
  maxObservationAgeMs = 30_000,
  futureSkewMs = 5_000
}) {
  if (!secret || Buffer.byteLength(secret) < 32) throw new Error("host secret must be at least 32 bytes");
  const context = validateHostInvocationContext(machineContext);
  const now = nowMs(clock);
  if (context.observed_at_ms > now + Number(futureSkewMs) || now - context.observed_at_ms > Number(maxObservationAgeMs)) {
    throw new Error("current-entry Host invocation context is stale");
  }
  const evidence = {
    schema_version: 1,
    provenance: CURRENT_ENTRY_PROVENANCE,
    entry_scope: CURRENT_ENTRY_SCOPE,
    machine_source: HOST_INVOCATION_CONTEXT_SOURCE,
    conversation_id: context.conversation_id,
    browser_target_id: context.browser_target_id,
    top_frame_id: context.top_frame_id,
    loader_id: context.loader_id,
    secure_origin: context.secure_origin,
    generation_anchor_sha256: context.generation_anchor_sha256,
    target_generation: positiveInteger(targetGeneration, "current_entry.target_generation"),
    ownership_generation: positiveInteger(ownershipGeneration, "current_entry.ownership_generation"),
    host_receipt_id: `hce_${randomUUID()}`,
    observed_at_unix_ms: context.observed_at_ms,
    issued_at_unix_ms: now,
    expires_at_unix_ms: now + positiveInteger(ttlMs, "current_entry.ttlMs")
  };
  return Object.freeze({ ...evidence, host_mac_sha256: hmac(secret, evidence) });
}

export function verifyCurrentEntryEvidence({
  secret,
  evidence,
  expectedTargetGeneration,
  expectedOwnershipGeneration,
  clock = Date.now,
  futureSkewMs = 5_000
}) {
  if (!secret || Buffer.byteLength(secret) < 32) throw new Error("host secret must be at least 32 bytes");
  if (!evidence || typeof evidence !== "object" || Array.isArray(evidence)) {
    throw new Error("current-entry evidence object is required");
  }
  const unsigned = { ...evidence };
  const mac = nonEmpty(unsigned.host_mac_sha256, "current_entry.host_mac_sha256");
  delete unsigned.host_mac_sha256;
  if (!secureEqualHex(mac, hmac(secret, unsigned))) {
    throw new Error("current-entry evidence authentication failed");
  }
  if (Number(evidence.schema_version) !== 1 || evidence.schema_version !== 1) throw new Error("current-entry schema_version is unsupported");
  if (evidence.provenance !== CURRENT_ENTRY_PROVENANCE) throw new Error("current-entry provenance is not trusted");
  if (evidence.entry_scope !== CURRENT_ENTRY_SCOPE) throw new Error("current-entry scope is not runtime invocation");
  if (evidence.machine_source !== HOST_INVOCATION_CONTEXT_SOURCE) throw new Error("current-entry machine source is not trusted");
  if (nonEmpty(evidence.secure_origin, "current_entry.secure_origin") !== "https://chatgpt.com") throw new Error("current-entry secure origin is invalid");
  const conversation = nonEmpty(evidence.conversation_id, "current_entry.conversation_id");
  if (!/^[0-9a-f-]{20,80}$/i.test(conversation)) throw new Error("current-entry conversation_id is invalid");
  nonEmpty(evidence.browser_target_id, "current_entry.browser_target_id");
  nonEmpty(evidence.top_frame_id, "current_entry.top_frame_id");
  nonEmpty(evidence.loader_id, "current_entry.loader_id");
  digest(evidence.generation_anchor_sha256, "current_entry.generation_anchor_sha256");
  nonEmpty(evidence.host_receipt_id, "current_entry.host_receipt_id");
  const targetGeneration = positiveInteger(evidence.target_generation, "current_entry.target_generation");
  const ownershipGeneration = positiveInteger(evidence.ownership_generation, "current_entry.ownership_generation");
  if (targetGeneration !== positiveInteger(expectedTargetGeneration, "expected target generation")) throw new Error("current-entry target generation mismatch");
  if (ownershipGeneration !== positiveInteger(expectedOwnershipGeneration, "expected ownership generation")) throw new Error("current-entry ownership generation mismatch");
  const observedAt = positiveInteger(evidence.observed_at_unix_ms, "current_entry.observed_at_unix_ms");
  const issuedAt = positiveInteger(evidence.issued_at_unix_ms, "current_entry.issued_at_unix_ms");
  const expiresAt = positiveInteger(evidence.expires_at_unix_ms, "current_entry.expires_at_unix_ms");
  const now = nowMs(clock);
  if (observedAt > now + Number(futureSkewMs) || issuedAt > now + Number(futureSkewMs)) throw new Error("current-entry evidence is from the future");
  if (expiresAt <= issuedAt || now > expiresAt) throw new Error("current-entry evidence expired");
  return Object.freeze({ ...evidence, host_mac_sha256: mac });
}

function normalizeInvocationOriginChallenge(input) {
  const challenge = {
    schema_version: Number(input?.schema_version),
    challenge_id: nonEmpty(input?.challenge_id, "invocation challenge_id"),
    challenge_nonce: nonEmpty(input?.challenge_nonce, "invocation challenge_nonce"),
    host_type: nonEmpty(input?.host_type, "invocation host_type"),
    invocation_id: nonEmpty(input?.invocation_id, "invocation_id"),
    operation: nonEmpty(input?.operation, "operation"),
    target_generation: positiveInteger(input?.target_generation, "target_generation"),
    ownership_generation: positiveInteger(input?.ownership_generation, "ownership_generation"),
    issued_at_ms: positiveInteger(input?.issued_at_ms, "issued_at_ms"),
    expires_at_ms: positiveInteger(input?.expires_at_ms, "expires_at_ms")
  };
  if (challenge.schema_version !== 1) throw new Error("invocation challenge schema_version is unsupported");
  if (challenge.host_type !== "chatgpt_web") throw new Error("invocation challenge host_type is unsupported");
  if (challenge.expires_at_ms <= challenge.issued_at_ms) throw new Error("invocation challenge expiry is invalid");
  return challenge;
}

export function signRuntimeInvocationOriginReceipt({
  secret,
  challenge,
  machineOrigin,
  hostEpoch,
  clock = Date.now,
  ttlMs = 15_000
}) {
  if (!secret || Buffer.byteLength(secret) < 32) throw new Error("host secret must be at least 32 bytes");
  const normalizedChallenge = normalizeInvocationOriginChallenge(challenge);
  const origin = validateMachineInvocationOrigin(machineOrigin);
  const epoch = nonEmpty(hostEpoch, "host_epoch");
  if (origin.invocation_id !== normalizedChallenge.invocation_id) {
    throw new Error("machine invocation_id does not match Runtime challenge");
  }
  if (origin.operation !== normalizedChallenge.operation) {
    throw new Error("machine invocation operation does not match Runtime challenge");
  }
  const issuedAt = nowMs(clock);
  const receipt = {
    schema_version: 1,
    receipt_id: `rio_${randomUUID()}`,
    provenance: INVOCATION_ORIGIN_PROVENANCE,
    host_type: origin.host_type,
    conversation_id: origin.conversation_id,
    browser_target_id: origin.browser_target_id,
    top_frame_id: origin.top_frame_id,
    loader_id: origin.loader_id,
    secure_origin: origin.secure_origin,
    invocation_id: origin.invocation_id,
    operation: origin.operation,
    challenge_id: normalizedChallenge.challenge_id,
    challenge_nonce_sha256: sha256Text(normalizedChallenge.challenge_nonce),
    target_generation: normalizedChallenge.target_generation,
    ownership_generation: normalizedChallenge.ownership_generation,
    observed_at_ms: origin.observed_at_ms,
    issued_at_ms: issuedAt,
    expires_at_ms: Math.min(
      issuedAt + positiveInteger(ttlMs, "invocation receipt ttlMs"),
      normalizedChallenge.expires_at_ms
    ),
    host_epoch: epoch,
    single_use: true,
    machine_origin_source: origin.source
  };
  return Object.freeze({ ...receipt, host_mac_sha256: hmac(secret, receipt) });
}

export class RuntimeInvocationOriginVerifier {
  constructor(secret, {
    clock = Date.now,
    challengeTtlMs = 30_000,
    receiptTtlMs = 15_000,
    attestationSkewMs = 5_000,
    hostEpoch,
    statePath = null
  } = {}) {
    if (!secret || Buffer.byteLength(secret) < 32) throw new Error("verifier secret must be at least 32 bytes");
    this.secret = secret;
    this.clock = clock;
    this.challengeTtlMs = positiveInteger(challengeTtlMs, "challengeTtlMs");
    this.receiptTtlMs = positiveInteger(receiptTtlMs, "receiptTtlMs");
    this.attestationSkewMs = Number(attestationSkewMs);
    this.hostEpoch = nonEmpty(hostEpoch, "hostEpoch");
    this.statePath = statePath;
    const persisted = loadPrivateState(statePath);
    this.challenges = new Map(Array.isArray(persisted?.challenges) ? persisted.challenges : []);
    this.usedReceipts = new Set(persisted?.used_receipts ?? []);
  }

  #persist() {
    savePrivateState(this.statePath, {
      schema_version: 1,
      challenges: [...this.challenges.entries()],
      used_receipts: [...this.usedReceipts]
    });
  }

  issueChallenge({ invocation_id, operation, target_generation, ownership_generation }) {
    const issuedAt = nowMs(this.clock);
    const challenge = Object.freeze({
      schema_version: 1,
      challenge_id: `ric_${randomUUID()}`,
      challenge_nonce: randomBytes(24).toString("hex"),
      host_type: "chatgpt_web",
      invocation_id: nonEmpty(invocation_id, "invocation_id"),
      operation: nonEmpty(operation, "operation"),
      target_generation: positiveInteger(target_generation, "target_generation"),
      ownership_generation: positiveInteger(ownership_generation, "ownership_generation"),
      issued_at_ms: issuedAt,
      expires_at_ms: issuedAt + this.challengeTtlMs
    });
    this.challenges.set(challenge.challenge_id, { challenge: structuredClone(challenge), consumed: false });
    this.#persist();
    return structuredClone(challenge);
  }

  verifyAndConsume(receipt, expected = {}) {
    const now = nowMs(this.clock);
    if (Number(receipt?.schema_version) !== 1 || receipt?.schema_version !== 1) {
      throw new Error("invocation origin receipt schema_version is unsupported");
    }
    const challengeId = nonEmpty(receipt?.challenge_id, "receipt.challenge_id");
    const challengeRecord = this.challenges.get(challengeId);
    if (!challengeRecord) throw new Error("unknown invocation origin challenge");
    if (challengeRecord.consumed) throw new Error("invocation origin challenge was already consumed");
    const challenge = normalizeInvocationOriginChallenge(challengeRecord.challenge);
    if (now > challenge.expires_at_ms) throw new Error("invocation origin challenge expired");

    const unsigned = { ...receipt };
    const mac = nonEmpty(unsigned.host_mac_sha256, "receipt.host_mac_sha256");
    delete unsigned.host_mac_sha256;
    if (!secureEqualHex(mac, hmac(this.secret, unsigned))) {
      throw new Error("invocation origin receipt authentication failed");
    }
    if (receipt.provenance !== INVOCATION_ORIGIN_PROVENANCE) throw new Error("untrusted invocation origin receipt provenance");
    if (receipt.host_type !== challenge.host_type) throw new Error("invocation origin host_type mismatch");
    if (receipt.invocation_id !== challenge.invocation_id) throw new Error("invocation_id does not match challenge");
    if (receipt.operation !== challenge.operation) throw new Error("invocation operation does not match challenge");
    if (receipt.target_generation !== challenge.target_generation) throw new Error("target generation does not match challenge");
    if (receipt.ownership_generation !== challenge.ownership_generation) throw new Error("ownership generation does not match challenge");
    if (receipt.challenge_nonce_sha256 !== sha256Text(challenge.challenge_nonce)) throw new Error("invocation challenge nonce mismatch");
    if (receipt.host_epoch !== this.hostEpoch) throw new Error("Host epoch mismatch after restart");
    if (receipt.single_use !== true) throw new Error("invocation origin receipt must be single-use");
    if (receipt.machine_origin_source !== MACHINE_INVOCATION_ORIGIN_SOURCE) throw new Error("invocation origin machine source mismatch");
    if (this.usedReceipts.has(receipt.receipt_id)) throw new Error("invocation origin receipt replayed");

    const origin = validateMachineInvocationOrigin({
      host_type: receipt.host_type,
      invocation_id: receipt.invocation_id,
      operation: receipt.operation,
      browser_target_id: receipt.browser_target_id,
      top_frame_id: receipt.top_frame_id,
      loader_id: receipt.loader_id,
      secure_origin: receipt.secure_origin,
      conversation_id: receipt.conversation_id,
      observed_at_ms: receipt.observed_at_ms,
      source: receipt.machine_origin_source
    });
    if (origin.observed_at_ms < challenge.issued_at_ms - this.attestationSkewMs || origin.observed_at_ms > now + this.attestationSkewMs) {
      throw new Error("invocation origin machine observation is not fresh for challenge");
    }
    if (receipt.issued_at_ms < challenge.issued_at_ms || receipt.issued_at_ms > now + this.attestationSkewMs) {
      throw new Error("invocation origin receipt issue time is not fresh");
    }
    if (receipt.expires_at_ms > challenge.expires_at_ms || receipt.expires_at_ms > receipt.issued_at_ms + this.receiptTtlMs || now > receipt.expires_at_ms) {
      throw new Error("invocation origin receipt expired or outlives challenge");
    }

    if (expected.invocation_id && receipt.invocation_id !== expected.invocation_id) throw new Error("wrong invocation_id");
    if (expected.operation && receipt.operation !== expected.operation) throw new Error("wrong invocation operation");
    if (expected.conversation_id && receipt.conversation_id !== expected.conversation_id) throw new Error("wrong invocation conversation");
    if (expected.target_generation && receipt.target_generation !== expected.target_generation) throw new Error("stale target generation");
    if (expected.ownership_generation && receipt.ownership_generation !== expected.ownership_generation) throw new Error("stale ownership generation");

    challengeRecord.consumed = true;
    this.usedReceipts.add(nonEmpty(receipt.receipt_id, "receipt.receipt_id"));
    this.#persist();
    return Object.freeze({ ...receipt });
  }
}


function privateOwnerMatches(stat) {
  return typeof process.getuid !== "function" || stat.uid === process.getuid();
}

function assertPrivateDirectory(path) {
  mkdirSync(path, { recursive: true, mode: 0o700 });
  const stat = lstatSync(path);
  if (!stat.isDirectory() || stat.isSymbolicLink() || !privateOwnerMatches(stat) || (stat.mode & 0o077) !== 0) {
    throw new Error("private state directory is unsafe");
  }
}

function assertPrivateStateFile(path) {
  const stat = lstatSync(path);
  if (!stat.isFile() || stat.isSymbolicLink() || !privateOwnerMatches(stat) || (stat.mode & 0o077) !== 0) {
    throw new Error("private state file is unsafe");
  }
}

function savePrivateState(path, value) {
  if (!path) return;
  const dir = dirname(path);
  assertPrivateDirectory(dir);
  if (existsSync(path)) assertPrivateStateFile(path);
  const temp = join(dir, `.${randomUUID()}.tmp`);
  let fd = null;
  try {
    fd = openSync(temp, "wx", 0o600);
    writeFileSync(fd, JSON.stringify(value));
    fsyncSync(fd);
    closeSync(fd);
    fd = null;
    renameSync(temp, path);
    chmodSync(path, 0o600);
    const dirFd = openSync(dir, "r");
    try { fsyncSync(dirFd); } finally { closeSync(dirFd); }
  } finally {
    if (fd != null) closeSync(fd);
    if (existsSync(temp)) {
      try { unlinkSync(temp); } catch {}
    }
  }
}

function loadPrivateState(path) {
  if (!path || !existsSync(path)) return null;
  assertPrivateDirectory(dirname(path));
  assertPrivateStateFile(path);
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    throw new Error("private state file is invalid");
  }
}

function withPrivateStateLock(path, callback) {
  if (!path) return callback();
  const dir = dirname(path);
  const lockPath = `${path}.lock`;
  assertPrivateDirectory(dir);
  let fd = null;
  try {
    try {
      fd = openSync(lockPath, "wx", 0o600);
      chmodSync(lockPath, 0o600);
      fsyncSync(fd);
    } catch (error) {
      if (error && error.code === "EEXIST") {
        assertPrivateStateFile(lockPath);
        throw new Error("receipt journal state lock is held");
      }
      throw error;
    }
    return callback();
  } finally {
    if (fd != null) {
      closeSync(fd);
      try { unlinkSync(lockPath); } finally {
        const dirFd = openSync(dir, "r");
        try { fsyncSync(dirFd); } finally { closeSync(dirFd); }
      }
    }
  }
}

function digest(value, name) {
  const text = nonEmpty(value, name);
  if (!/^[0-9a-f]{64}$/.test(text)) throw new Error(`${name} must be a SHA-256 hex digest`);
  return text;
}

const RUN_COMMAND_DIGEST_PROFILE = "lab_run_command_digest_v1";

function requestDigestProfile(value) {
  const profile = receiptText(value, "request_digest_profile");
  if (profile !== RUN_COMMAND_DIGEST_PROFILE) {
    throw new Error("request_digest_profile is unsupported");
  }
  return profile;
}

function retryOuterRequestDigest(input) {
  return digest(input?.retry_outer_request_sha256, "retry_outer_request_sha256");
}

function receiptText(value, name, prefix) {
  const text = nonEmpty(value, name);
  if (prefix && !text.startsWith(prefix)) throw new Error(`${name} has an invalid prefix`);
  return text;
}

function normalizeReceiptMachineContext(input) {
  const context = {
    conversation_id: nonEmpty(input?.conversation_id, "machine_context.conversation_id"),
    browser_target_id: nonEmpty(input?.browser_target_id, "machine_context.browser_target_id"),
    top_frame_id: nonEmpty(input?.top_frame_id, "machine_context.top_frame_id"),
    loader_id: nonEmpty(input?.loader_id, "machine_context.loader_id"),
    secure_origin: nonEmpty(input?.secure_origin, "machine_context.secure_origin"),
    generation_anchor_sha256: digest(input?.generation_anchor_sha256, "machine_context.generation_anchor_sha256"),
    extension_binding_sha256: digest(input?.extension_binding_sha256, "machine_context.extension_binding_sha256"),
    extension_instance_id_sha256: digest(input?.extension_instance_id_sha256, "machine_context.extension_instance_id_sha256"),
    extension_loaded_at_ms: positiveInteger(input?.extension_loaded_at_ms, "machine_context.extension_loaded_at_ms")
  };
  if (context.secure_origin !== "https://chatgpt.com") throw new Error("machine_context secure origin is invalid");
  if (!/^[0-9a-f-]{20,80}$/i.test(context.conversation_id)) throw new Error("machine_context conversation is invalid");
  return context;
}

function normalizeReceiptBinding(input) {
  const binding = {
    request_digest_profile: requestDigestProfile(input?.request_digest_profile),
    bridge_call_id: receiptText(input?.bridge_call_id, "bridge_call_id", "bc_"),
    bridge_challenge_nonce: receiptText(input?.bridge_challenge_nonce, "bridge_challenge_nonce"),
    transport_binding_id: receiptText(input?.transport_binding_id, "transport_binding_id", "tb_"),
    workspace: receiptText(input?.workspace, "workspace"),
    production_release_revision: receiptText(input?.production_release_revision, "production_release_revision"),
    production_endpoint_generation: positiveInteger(input?.production_endpoint_generation, "production_endpoint_generation"),
    bridge_principal_id: receiptText(input?.bridge_principal_id, "bridge_principal_id"),
    bridge_execution_session_id: receiptText(input?.bridge_execution_session_id, "bridge_execution_session_id"),
    bridge_capability_id: receiptText(input?.bridge_capability_id, "bridge_capability_id"),
    bridge_workspace_id: receiptText(input?.bridge_workspace_id, "bridge_workspace_id"),
    tool_name: receiptText(input?.tool_name, "tool_name"),
    outer_request_sha256: digest(input?.outer_request_sha256, "outer_request_sha256"),
    forwarded_request_sha256: digest(input?.forwarded_request_sha256, "forwarded_request_sha256"),
    normalized_request_sha256: digest(input?.normalized_request_sha256, "normalized_request_sha256"),
    ...normalizeReceiptMachineContext(input?.machine_context)
  };
  if (!/^[0-9a-f]{40}$/.test(binding.production_release_revision)) {
    throw new Error("production_release_revision must be a 40-hex revision");
  }
  if (binding.tool_name !== "run_command") throw new Error("tool_name is unsupported");
  return binding;
}

function validateBridgeBindingContract(binding) {
  if (binding.workspace !== "/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge") {
    throw new Error("workspace must be the canonical Local Agent Bridge workspace");
  }
  if (binding.bridge_workspace_id !== "local-agent-bridge-production") {
    throw new Error("bridge_workspace_id is invalid");
  }
  return binding;
}

function bindingFromSignedReceipt(receipt) {
  return normalizeReceiptBinding({ ...receipt, machine_context: receipt });
}

function sameReceiptBinding(left, right) {
  return Object.keys(left).every((key) => left[key] === right[key]);
}

function signedReceipt(secret, value) {
  return Object.freeze({ ...value, host_mac_sha256: hmac(secret, value) });
}

function authenticateReceipt(secret, receipt, provenance) {
  const unsigned = { ...(receipt || {}) };
  const mac = nonEmpty(unsigned.host_mac_sha256, "host_mac_sha256");
  delete unsigned.host_mac_sha256;
  if (unsigned.provenance !== provenance || !secureEqualHex(mac, hmac(secret, unsigned))) {
    throw new Error("Host receipt authentication failed");
  }
  return Object.freeze({ ...unsigned, host_mac_sha256: mac });
}

function validateJournalRecord(secret, record) {
  if (!record || typeof record !== "object" || !["ISSUED", "HOST_VALIDATED_IN_FLIGHT", "TERMINAL_PENDING", "TERMINAL"].includes(record.state)) {
    throw new Error("receipt journal state is invalid");
  }
  if (!record.binding || record.binding.request_digest_profile !== RUN_COMMAND_DIGEST_PROFILE ||
      !record.capability || record.capability.request_digest_profile !== RUN_COMMAND_DIGEST_PROFILE) {
    throw new Error("receipt journal state has an unsupported request_digest_profile");
  }
  authenticateReceipt(secret, record.capability, "lab_host_invocation_capability_v1");
  if (record.pre) {
    if (record.pre.request_digest_profile !== RUN_COMMAND_DIGEST_PROFILE ||
        !/^[0-9a-f]{64}$/.test(String(record.retry_outer_request_sha256 || ""))) {
      throw new Error("receipt journal state has an unsupported pre receipt profile");
    }
    authenticateReceipt(secret, record.pre, "lab_host_tool_pre_receipt_v1");
  }
}

export class ToolInvocationReceiptJournal {
  constructor(secret, { clock = Date.now, capabilityTtlMs = 15_000, statePath = null } = {}) {
    if (!secret || Buffer.byteLength(secret) < 32) throw new Error("receipt journal secret must be at least 32 bytes");
    this.secret = secret;
    this.clock = clock;
    this.capabilityTtlMs = positiveInteger(capabilityTtlMs, "capabilityTtlMs");
    this.statePath = statePath;
    this.records = new Map();
    this.#reload();
  }

  #reload() {
    if (!this.statePath) return;
    const persisted = loadPrivateState(this.statePath);
    if (persisted && persisted.schema_version !== 1) throw new Error("receipt journal state schema is unsupported");
    this.records = new Map(Array.isArray(persisted?.records) ? persisted.records : []);
    for (const record of this.records.values()) validateJournalRecord(this.secret, record);
  }

  #persist() {
    savePrivateState(this.statePath, { schema_version: 1, records: [...this.records.entries()] });
  }

  #mutate(callback) {
    return withPrivateStateLock(this.statePath, () => {
      this.#reload();
      const result = callback();
      this.#persist();
      return result;
    });
  }

  issueCapability(input) {
    const binding = validateBridgeBindingContract(normalizeReceiptBinding(input));
    return this.#mutate(() => {
      const issuedAt = nowMs(this.clock);
      const capability = signedReceipt(this.secret, {
        schema_version: 1,
        provenance: "lab_host_invocation_capability_v1",
        capability_id: `hic_${randomUUID()}`,
        ...binding,
        issued_at_unix_ms: issuedAt,
        expires_at_unix_ms: issuedAt + this.capabilityTtlMs
      });
      this.records.set(capability.capability_id, { state: "ISSUED", binding, capability, pre: null, terminal: null });
      return structuredClone(capability);
    });
  }

  consumeCapability(input) {
    const capability = authenticateReceipt(this.secret, input?.capability, "lab_host_invocation_capability_v1");
    const binding = normalizeReceiptBinding(input);
    const retryOuterRequestSha256 = retryOuterRequestDigest(input);
    const executionId = receiptText(input?.host_tool_execution_id, "host_tool_execution_id", "hte_");
    return this.#mutate(() => {
      const record = this.records.get(receiptText(capability.capability_id, "capability_id", "hic_"));
      if (!record) throw new Error("capability replay or unknown capability");
      if (record.state !== "ISSUED") throw new Error("capability replayed");
      const signedBinding = validateBridgeBindingContract(bindingFromSignedReceipt(capability));
      if (!sameReceiptBinding(record.binding, binding) || !sameReceiptBinding(signedBinding, binding)) {
        throw new Error("capability request or machine context mismatch");
      }
      const now = nowMs(this.clock);
      if (now >= capability.expires_at_unix_ms) throw new Error("capability expired");
      const pre = signedReceipt(this.secret, {
        schema_version: 1,
        provenance: "lab_host_tool_pre_receipt_v1",
        pre_receipt_id: `hpr_${randomUUID()}`,
        capability_id: capability.capability_id,
        host_tool_execution_id: executionId,
        ...binding,
        retry_outer_request_sha256: retryOuterRequestSha256,
        pre_nonce: randomBytes(24).toString("hex"),
        issued_at_unix_ms: now,
        host_epoch_binding_sha256: sha256Text(`${binding.extension_binding_sha256}:${binding.extension_instance_id_sha256}:${binding.extension_loaded_at_ms}`)
      });
      record.state = "HOST_VALIDATED_IN_FLIGHT";
      record.pre = pre;
      record.pre_receipt_sha256 = sha256Text(canonicalJson(pre));
      record.retry_outer_request_sha256 = retryOuterRequestSha256;
      return structuredClone(pre);
    });
  }

  terminalize(input) {
    const pre = authenticateReceipt(this.secret, input?.pre_receipt, "lab_host_tool_pre_receipt_v1");
    requestDigestProfile(pre.request_digest_profile);
    const binding = normalizeReceiptBinding(input);
    const retryOuterRequestSha256 = retryOuterRequestDigest(input);
    const backendRoute = receiptText(input?.backend_route, "backend_route");
    if (!backendRoute.startsWith("/")) throw new Error("backend_route must be an absolute path");
    const httpStatus = Number(input?.http_status);
    if (!Number.isSafeInteger(httpStatus) || httpStatus < 100 || httpStatus > 599) throw new Error("http_status is invalid");
    const responseBodySha256 = digest(input?.response_body_sha256, "response_body_sha256");
    const classification = receiptText(input?.terminal_classification, "terminal_classification");
    return this.#mutate(() => {
      const record = this.records.get(receiptText(pre.capability_id, "pre_receipt.capability_id", "hic_"));
      if (!record || record.state !== "HOST_VALIDATED_IN_FLIGHT" || !record.pre) {
        throw new Error("pre receipt replay or mismatch");
      }
      if (record.pre.pre_receipt_id !== pre.pre_receipt_id ||
          record.pre_receipt_sha256 !== sha256Text(canonicalJson(pre))) {
        throw new Error("pre receipt mismatch");
      }
      if (retryOuterRequestSha256 !== record.retry_outer_request_sha256 ||
          retryOuterRequestSha256 !== pre.retry_outer_request_sha256) {
        throw new Error("terminal retry outer request digest mismatch");
      }
      if (!sameReceiptBinding(record.binding, binding) || pre.host_tool_execution_id !== record.pre.host_tool_execution_id) {
        throw new Error("terminal request mismatch");
      }
      record.state = "TERMINAL_PENDING";
      this.#persist();
      const terminal = signedReceipt(this.secret, {
        schema_version: 1,
        provenance: "lab_host_tool_terminal_receipt_v1",
        terminal_receipt_id: `htr_${randomUUID()}`,
        pre_receipt_id: pre.pre_receipt_id,
        pre_receipt_sha256: record.pre_receipt_sha256,
        capability_id: pre.capability_id,
        host_tool_execution_id: pre.host_tool_execution_id,
        ...binding,
        retry_outer_request_sha256: retryOuterRequestSha256,
        backend_route: backendRoute,
        http_status: httpStatus,
        response_body_sha256: responseBodySha256,
        terminal_classification: classification,
        issued_at_unix_ms: nowMs(this.clock),
        host_epoch_binding_sha256: pre.host_epoch_binding_sha256
      });
      record.state = "TERMINAL";
      record.terminal = terminal;
      return structuredClone(terminal);
    });
  }

  inspectStateForTest() {
    return structuredClone({ schema_version: 1, records: [...this.records.entries()] });
  }
}

const PRE_KEYS = [
  "schema_version", "provenance", "request_digest_profile", "pre_receipt_id", "capability_id", "host_tool_execution_id",
  "bridge_call_id", "bridge_challenge_nonce", "transport_binding_id", "workspace", "production_release_revision",
  "production_endpoint_generation", "bridge_principal_id", "bridge_execution_session_id", "bridge_capability_id",
  "bridge_workspace_id", "tool_name", "outer_request_sha256", "forwarded_request_sha256", "normalized_request_sha256",
  "retry_outer_request_sha256", "conversation_id", "browser_target_id", "top_frame_id", "loader_id",
  "generation_anchor_sha256", "secure_origin", "extension_binding_sha256", "extension_instance_id_sha256",
  "extension_loaded_at_ms", "pre_nonce", "issued_at_unix_ms", "host_epoch_binding_sha256", "host_mac_sha256"
].sort();
const TERMINAL_KEYS = [
  "schema_version", "provenance", "request_digest_profile", "terminal_receipt_id", "pre_receipt_id", "pre_receipt_sha256",
  "capability_id", "host_tool_execution_id", "bridge_call_id", "bridge_challenge_nonce", "transport_binding_id",
  "workspace", "production_release_revision", "production_endpoint_generation", "bridge_principal_id",
  "bridge_execution_session_id", "bridge_capability_id", "bridge_workspace_id", "tool_name", "outer_request_sha256",
  "forwarded_request_sha256", "normalized_request_sha256", "retry_outer_request_sha256", "conversation_id",
  "browser_target_id", "top_frame_id", "loader_id", "generation_anchor_sha256", "secure_origin",
  "extension_binding_sha256", "extension_instance_id_sha256", "extension_loaded_at_ms", "backend_route", "http_status",
  "response_body_sha256", "terminal_classification", "issued_at_unix_ms", "host_epoch_binding_sha256", "host_mac_sha256"
].sort();

function exactKeys(value, keys, label) {
  const actual = value && typeof value === "object" && !Array.isArray(value) ? Object.keys(value).sort() : [];
  if (actual.length !== keys.length || actual.some((key, index) => key !== keys[index])) {
    throw new Error(`${label} keys are invalid`);
  }
}

function verifyToolReceipt(input, keys, provenance) {
  const { secret, receipt, receipt_sha256, expected_request_digests: expected, clock = Date.now,
    maxReceiptAgeMs = 15_000, futureSkewMs = 5_000 } = input || {};
  exactKeys(receipt, keys, "tool receipt");
  const signed = authenticateReceipt(secret, receipt, provenance);
  const binding = validateBridgeBindingContract(bindingFromSignedReceipt(signed));
  if (signed.schema_version !== 1 || signed.request_digest_profile !== RUN_COMMAND_DIGEST_PROFILE ||
      signed.tool_name !== "run_command") throw new Error("tool receipt schema is invalid");
  const now = nowMs(clock);
  const issued = positiveInteger(signed.issued_at_unix_ms, "issued_at_unix_ms");
  if (issued > now + futureSkewMs || now - issued > maxReceiptAgeMs) throw new Error("tool receipt is stale");
  const epochBinding = sha256Text(`${binding.extension_binding_sha256}:${binding.extension_instance_id_sha256}:${binding.extension_loaded_at_ms}`);
  if (signed.host_epoch_binding_sha256 !== epochBinding) throw new Error("tool receipt Host epoch binding mismatch");
  if (digest(receipt_sha256, "receipt_sha256") !== sha256Text(canonicalJson(signed))) throw new Error("receipt_sha256 mismatch");
  exactKeys(expected, ["forwarded_request_sha256", "normalized_request_sha256", "outer_request_sha256", "retry_outer_request_sha256"], "expected_request_digests");
  for (const key of Object.keys(expected)) {
    if (signed[key] !== digest(expected[key], `expected ${key}`)) throw new Error(`${key} mismatch`);
  }
  return signed;
}

export function verifyToolPreReceipt(input) {
  const receipt = verifyToolReceipt(input, PRE_KEYS, "lab_host_tool_pre_receipt_v1");
  receiptText(receipt.pre_receipt_id, "pre_receipt_id", "hpr_");
  receiptText(receipt.capability_id, "capability_id", "hic_");
  receiptText(receipt.host_tool_execution_id, "host_tool_execution_id", "hte_");
  if (!/^[0-9a-f]{48}$/.test(receipt.pre_nonce)) throw new Error("pre_nonce is invalid");
  return Object.freeze({ ...receipt });
}

export function verifyToolTerminalReceipt(input) {
  const receipt = verifyToolReceipt(input, TERMINAL_KEYS, "lab_host_tool_terminal_receipt_v1");
  receiptText(receipt.terminal_receipt_id, "terminal_receipt_id", "htr_");
  receiptText(receipt.pre_receipt_id, "pre_receipt_id", "hpr_");
  receiptText(receipt.capability_id, "capability_id", "hic_");
  receiptText(receipt.host_tool_execution_id, "host_tool_execution_id", "hte_");
  const chain = input?.expected_pre_chain;
  exactKeys(chain, ["capability_id", "host_tool_execution_id", "pre_receipt_id", "pre_receipt_sha256"], "expected_pre_chain");
  for (const key of ["pre_receipt_id", "capability_id", "host_tool_execution_id"]) {
    if (receipt[key] !== chain[key]) throw new Error(`terminal ${key} mismatch`);
  }
  if (receipt.pre_receipt_sha256 !== digest(chain.pre_receipt_sha256, "expected_pre_chain.pre_receipt_sha256")) {
    throw new Error("terminal pre_receipt_sha256 mismatch");
  }
  if (!String(receipt.backend_route).startsWith("/")) throw new Error("terminal backend_route is invalid");
  if (!Number.isSafeInteger(receipt.http_status) || receipt.http_status < 100 || receipt.http_status > 599) {
    throw new Error("terminal http_status is invalid");
  }
  digest(receipt.response_body_sha256, "response_body_sha256");
  receiptText(receipt.terminal_classification, "terminal_classification");
  return Object.freeze({ ...receipt });
}

export class RuntimeHostVerifier {
  constructor(secret, { clock = Date.now, challengeTtlMs = 30_000, attestationSkewMs = 5_000, verifiedTargetTtlMs = 30_000, statePath = null } = {}) {
    if (!secret || Buffer.byteLength(secret) < 32) throw new Error("verifier secret must be at least 32 bytes");
    this.secret = secret;
    this.clock = clock;
    this.challengeTtlMs = challengeTtlMs;
    this.attestationSkewMs = attestationSkewMs;
    this.verifiedTargetTtlMs = verifiedTargetTtlMs;
    this.statePath = statePath;
    const persisted = loadPrivateState(statePath);
    this.challenges = new Map(
      Array.isArray(persisted?.challenges)
        ? persisted.challenges.map(([id, record]) => [id, record])
        : []
    );
    this.usedAttestationReceipts = new Set(persisted?.used_attestation_receipts ?? []);
    this.verifiedTargets = new Map(
      Array.isArray(persisted?.verified_targets) ? persisted.verified_targets : []
    );
  }

  #persist() {
    savePrivateState(this.statePath, {
      schema_version: 1,
      challenges: [...this.challenges.entries()],
      used_attestation_receipts: [...this.usedAttestationReceipts],
      verified_targets: [...this.verifiedTargets.entries()]
    });
  }

  issueChallenge({ conversation_id, target_generation, ownership_generation }) {
    const issuedAt = nowMs(this.clock);
    const challenge = {
      schema_version: 1,
      challenge_id: `hc_${randomUUID()}`,
      challenge_nonce: randomBytes(24).toString("hex"),
      host_type: "chatgpt_web",
      conversation_id: nonEmpty(conversation_id, "conversation_id"),
      target_generation: positiveInteger(target_generation, "target_generation"),
      ownership_generation: positiveInteger(ownership_generation, "ownership_generation"),
      issued_at_ms: issuedAt,
      expires_at_ms: issuedAt + this.challengeTtlMs
    };
    this.challenges.set(challenge.challenge_id, { challenge, consumed: false });
    this.#persist();
    return structuredClone(challenge);
  }

  verifyAndConsume(attestation, expected = {}) {
    const now = nowMs(this.clock);
    const challengeId = nonEmpty(attestation?.challenge_id, "attestation.challenge_id");
    const challengeRecord = this.challenges.get(challengeId);
    if (!challengeRecord) throw new Error("unknown host attestation challenge");
    if (challengeRecord.consumed) throw new Error("host attestation challenge was already consumed");
    const challenge = challengeRecord.challenge;
    if (now > challenge.expires_at_ms) throw new Error("host attestation challenge expired");

    const unsigned = { ...attestation };
    const mac = nonEmpty(unsigned.host_mac_sha256, "host_mac_sha256");
    delete unsigned.host_mac_sha256;
    const expectedMac = hmac(this.secret, unsigned);
    if (!secureEqualHex(mac, expectedMac)) throw new Error("Host attestation authentication failed");

    if (attestation.provenance !== ATTESTATION_PROVENANCE) throw new Error("untrusted host attestation provenance");
    if (attestation.host_type !== challenge.host_type) throw new Error("host_type does not match challenge");
    if (attestation.challenge_nonce !== challenge.challenge_nonce) throw new Error("host challenge nonce mismatch");
    if (attestation.conversation_id !== challenge.conversation_id) throw new Error("conversation_id does not match challenge");
    if (attestation.target_generation !== challenge.target_generation) throw new Error("target generation does not match challenge");
    if (attestation.ownership_generation !== challenge.ownership_generation) throw new Error("ownership generation does not match challenge");
    if (expected.conversation_id && attestation.conversation_id !== expected.conversation_id) throw new Error("wrong Web conversation");
    if (expected.target_generation && attestation.target_generation !== expected.target_generation) throw new Error("stale target generation");
    if (expected.ownership_generation && attestation.ownership_generation !== expected.ownership_generation) throw new Error("stale ownership generation");

    const observation = validateMachineObservation(attestation.machine_observation);
    if (observation.conversation_id !== attestation.conversation_id) throw new Error("machine observation conversation mismatch");
    if (observation.browser_target_id !== attestation.browser_target_id) throw new Error("machine observation browser target mismatch");
    if (attestation.observed_at_ms !== observation.observed_at_ms) throw new Error("attestation observation timestamp mismatch");
    if (attestation.observed_at_ms < challenge.issued_at_ms - this.attestationSkewMs || attestation.observed_at_ms > now + this.attestationSkewMs) {
      throw new Error("host attestation machine observation is not fresh for challenge");
    }
    if (attestation.issued_at_ms < challenge.issued_at_ms || attestation.issued_at_ms > now + this.attestationSkewMs) {
      throw new Error("host attestation issue time is not fresh");
    }
    if (attestation.expires_at_ms > challenge.expires_at_ms || now > attestation.expires_at_ms) {
      throw new Error("host attestation expired or outlives Runtime challenge");
    }
    if (this.usedAttestationReceipts.has(attestation.host_receipt_id)) throw new Error("host attestation receipt replayed");

    challengeRecord.consumed = true;
    this.usedAttestationReceipts.add(attestation.host_receipt_id);
    const verified = signVerifiedTarget(this.secret, {
      schema_version: 1,
      verified_target_id: `vt_${randomUUID()}`,
      host_type: attestation.host_type,
      conversation_id: attestation.conversation_id,
      browser_target_id: observation.browser_target_id,
      top_frame_id: observation.top_frame_id,
      loader_id: observation.loader_id,
      secure_origin: observation.secure_origin,
      target_generation: attestation.target_generation,
      ownership_generation: attestation.ownership_generation,
      host_receipt_id: attestation.host_receipt_id,
      verified_at_ms: now,
      provenance: VERIFIED_TARGET_PROVENANCE
    });
    this.verifiedTargets.set(attestation.host_receipt_id, verified);
    this.#persist();
    return verified;
  }

  consumeVerifiedTarget(hostReceiptId, expected = {}) {
    const receiptId = nonEmpty(hostReceiptId, "host_receipt_id");
    const storedVerified = this.verifiedTargets.get(receiptId);
    if (!storedVerified) throw new Error("verified Host target receipt is missing or already consumed");
    const verified = authenticateVerifiedTarget(this.secret, storedVerified);
    const now = nowMs(this.clock);
    if (now - Number(verified.verified_at_ms) > this.verifiedTargetTtlMs) {
      this.verifiedTargets.delete(receiptId);
      this.#persist();
      throw new Error("verified Host target receipt expired");
    }
    if (expected.conversation_id && verified.conversation_id !== expected.conversation_id) {
      throw new Error("verified Host target conversation mismatch");
    }
    if (expected.target_generation && verified.target_generation !== expected.target_generation) {
      throw new Error("verified Host target generation mismatch");
    }
    if (expected.ownership_generation && verified.ownership_generation !== expected.ownership_generation) {
      throw new Error("verified Host target ownership generation mismatch");
    }
    this.verifiedTargets.delete(receiptId);
    this.#persist();
    return structuredClone(verified);
  }
}

export function signHostAttestation({ secret, challenge, observation, clock = Date.now, ttlMs = 15_000, hostNonce }) {
  if (!secret || Buffer.byteLength(secret) < 32) throw new Error("host secret must be at least 32 bytes");
  const machine = validateMachineObservation(observation);
  const issuedAt = nowMs(clock);
  const envelope = {
    schema_version: 1,
    attestation_id: `ha_${randomUUID()}`,
    host_type: "chatgpt_web",
    conversation_id: machine.conversation_id,
    browser_target_id: machine.browser_target_id,
    challenge_id: nonEmpty(challenge?.challenge_id, "challenge_id"),
    challenge_nonce: nonEmpty(challenge?.challenge_nonce, "challenge_nonce"),
    host_nonce: hostNonce ? nonEmpty(hostNonce, "host_nonce") : randomBytes(24).toString("hex"),
    target_generation: positiveInteger(challenge?.target_generation, "target_generation"),
    ownership_generation: positiveInteger(challenge?.ownership_generation, "ownership_generation"),
    observed_at_ms: machine.observed_at_ms,
    issued_at_ms: issuedAt,
    expires_at_ms: Math.min(issuedAt + ttlMs, positiveInteger(challenge?.expires_at_ms, "challenge.expires_at_ms")),
    host_receipt_id: `hr_${randomUUID()}`,
    machine_observation: machine,
    provenance: ATTESTATION_PROVENANCE
  };
  if (envelope.conversation_id !== challenge.conversation_id) throw new Error("machine conversation does not match Runtime challenge");
  return { ...envelope, host_mac_sha256: hmac(secret, envelope) };
}

function sameTarget(observation, verified) {
  return (
    observation.host_type === verified.host_type &&
    observation.conversation_id === verified.conversation_id &&
    observation.browser_target_id === verified.browser_target_id &&
    observation.top_frame_id === verified.top_frame_id &&
    observation.loader_id === verified.loader_id &&
    observation.secure_origin === verified.secure_origin
  );
}

export class WebReentryAdapter {
  constructor(secret, driver, { clock = Date.now, statePath = null } = {}) {
    if (!secret || Buffer.byteLength(secret) < 32) throw new Error("re-entry secret must be at least 32 bytes");
    this.secret = secret;
    this.driver = driver;
    this.clock = clock;
    this.statePath = statePath;
    const persisted = loadPrivateState(statePath);
    this.legacyUsedWakeIds = new Set(
      persisted?.legacy_used_wake_ids ?? persisted?.used_wake_ids ?? []
    );
    this.wakeRecords = new Map(
      Array.isArray(persisted?.wake_records) ? persisted.wake_records : []
    );
    this.activeWakeIds = new Set();
  }

  #persist() {
    const terminalWakeIds = [...this.wakeRecords.entries()]
      .filter(([, record]) => record?.phase === "terminal")
      .map(([wakeId]) => wakeId);
    savePrivateState(this.statePath, {
      schema_version: 2,
      used_wake_ids: [...new Set([...this.legacyUsedWakeIds, ...terminalWakeIds])],
      legacy_used_wake_ids: [...this.legacyUsedWakeIds],
      wake_records: [...this.wakeRecords.entries()]
    });
  }

  #wakeBinding({ wakeId, nonce, payload, verified }) {
    return {
      wake_id: wakeId,
      wake_nonce_sha256: sha256Text(nonce),
      continuation_payload_sha256: sha256Text(payload),
      conversation_id: verified.conversation_id,
      browser_target_id: verified.browser_target_id,
      target_generation: verified.target_generation,
      ownership_generation: verified.ownership_generation
    };
  }

  #assertWakeBinding(record, binding) {
    for (const key of [
      "wake_id",
      "wake_nonce_sha256",
      "continuation_payload_sha256",
      "conversation_id",
      "browser_target_id",
      "target_generation",
      "ownership_generation"
    ]) {
      if (record?.[key] !== binding[key]) throw new Error("wake_id replayed with mismatched binding");
    }
  }

  #storeInFlight(binding, verified) {
    this.wakeRecords.set(binding.wake_id, {
      schema_version: 1,
      ...binding,
      verified_target: verified,
      phase: "in_flight",
      started_at_ms: nowMs(this.clock)
    });
    this.#persist();
  }

  #storeTerminal(wakeId, receipt) {
    const current = this.wakeRecords.get(wakeId) ?? {};
    this.wakeRecords.set(wakeId, {
      ...current,
      phase: "terminal",
      receipt,
      completed_at_ms: nowMs(this.clock)
    });
    this.#persist();
    return receipt;
  }

  #releaseWake(wakeId) {
    this.wakeRecords.delete(wakeId);
    this.#persist();
  }

  #recoverPersistedInFlight(record) {
    const verified = authenticateVerifiedTarget(this.secret, record.verified_target);
    const receipt = this.#signedReceipt({
      wakeId: record.wake_id,
      verified,
      wake_nonce_sha256: record.wake_nonce_sha256,
      continuation_payload_sha256: record.continuation_payload_sha256,
      dispatch_attempted: true,
      submit_confirmed: false,
      retryable: false,
      auto_retry_allowed: false,
      result_class: "RESULT_UNKNOWN",
      status: "result_unknown_after_restart",
      host_submit_receipt: null,
      submit_diagnostics: null
    });
    return this.#storeTerminal(record.wake_id, receipt);
  }

  #preDispatchReceipt({ wakeId, nonce, verified, payload, retryable, status, diagnostics = null }) {
    return this.#signedReceipt({
      wakeId,
      nonce,
      verified,
      payload,
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: retryable === true,
      auto_retry_allowed: retryable === true,
      result_class: "CONFIRMED_FAILURE_BEFORE_DISPATCH",
      status,
      host_submit_receipt: null,
      submit_diagnostics: diagnostics
    });
  }

  async reenter({ verified_target, wake_id, wake_nonce, continuation_payload, expected_target_generation, expected_ownership_generation }) {
    const wakeId = nonEmpty(wake_id, "wake_id");
    const verified = authenticateVerifiedTarget(this.secret, verified_target);
    if (verified.target_generation !== positiveInteger(expected_target_generation, "expected_target_generation")) throw new Error("stale target generation before re-entry");
    if (verified.ownership_generation !== positiveInteger(expected_ownership_generation, "expected_ownership_generation")) throw new Error("stale ownership generation before re-entry");
    const payload = nonEmpty(continuation_payload, "continuation_payload");
    const nonce = nonEmpty(wake_nonce, "wake_nonce");
    const binding = this.#wakeBinding({ wakeId, nonce, payload, verified });

    if (this.activeWakeIds.has(wakeId)) throw new Error("wake_id replayed");
    if (this.legacyUsedWakeIds.has(wakeId)) throw new Error("wake_id replayed");
    const existing = this.wakeRecords.get(wakeId);
    if (existing) {
      this.#assertWakeBinding(existing, binding);
      if (existing.phase === "in_flight") return this.#recoverPersistedInFlight(existing);
      throw new Error("wake_id replayed");
    }

    let pre;
    try {
      pre = validateMachineObservation(await this.driver.observeTarget(verified.browser_target_id));
    } catch (error) {
      return this.#preDispatchReceipt({
        wakeId,
        nonce,
        verified,
        payload,
        retryable: error?.retryable === true,
        status: error?.retryable === true ? "target_preflight_unavailable" : "target_preflight_failed"
      });
    }
    if (!sameTarget(pre, verified)) {
      return this.#preDispatchReceipt({
        wakeId,
        nonce,
        verified,
        payload,
        retryable: false,
        status: "target_changed_before_reentry"
      });
    }

    // Claim after the asynchronous preflight, then re-check to close same-process races.
    if (this.activeWakeIds.has(wakeId) || this.legacyUsedWakeIds.has(wakeId) || this.wakeRecords.has(wakeId)) {
      throw new Error("wake_id replayed");
    }
    this.activeWakeIds.add(wakeId);
    try {
      this.#storeInFlight(binding, verified);
    } catch (error) {
      this.activeWakeIds.delete(wakeId);
      this.wakeRecords.delete(wakeId);
      throw error;
    }

    let receipt;
    try {
      let submit;
      try {
        submit = await this.driver.submitContinuation({
          browser_target_id: verified.browser_target_id,
          conversation_id: verified.conversation_id,
          payload,
          wake_id: wakeId,
          wake_nonce: nonce,
          target_generation: verified.target_generation,
          ownership_generation: verified.ownership_generation
        });
      } catch (error) {
        const confirmedBeforeDispatch = error?.dispatch_attempted === false;
        if (confirmedBeforeDispatch) {
          const retryable = error?.retryable === true;
          receipt = this.#preDispatchReceipt({
            wakeId,
            nonce,
            verified,
            payload,
            retryable,
            status: retryable ? "transient_unavailable" : "submit_failed_before_dispatch",
            diagnostics: error?.submit_diagnostics ?? null
          });
          if (retryable) this.#releaseWake(wakeId);
          else this.#storeTerminal(wakeId, receipt);
          return receipt;
        }
        receipt = this.#signedReceipt({
          wakeId,
          nonce,
          verified,
          payload,
          dispatch_attempted: true,
          submit_confirmed: false,
          retryable: false,
          auto_retry_allowed: false,
          result_class: "RESULT_UNKNOWN",
          status: "result_unknown",
          host_submit_receipt: null,
          submit_diagnostics: error?.submit_diagnostics ?? null
        });
        return this.#storeTerminal(wakeId, receipt);
      }

      const dispatchSignal = submit?.dispatch_attempted;
      const confirmationSignal = submit?.submit_confirmed;
      const explicitConfirmed = dispatchSignal === true && confirmationSignal === true;
      const explicitPreDispatchFailure = dispatchSignal === false && confirmationSignal === false;

      if (explicitPreDispatchFailure) {
        const retryable = submit?.retryable === true;
        receipt = this.#preDispatchReceipt({
          wakeId,
          nonce,
          verified,
          payload,
          retryable,
          status: submit?.status || "submit_failed_before_dispatch",
          diagnostics: submit?.submit_diagnostics ?? null
        });
        if (retryable) this.#releaseWake(wakeId);
        else this.#storeTerminal(wakeId, receipt);
        return receipt;
      }

      if (!explicitConfirmed) {
        receipt = this.#signedReceipt({
          wakeId,
          nonce,
          verified,
          payload,
          dispatch_attempted: true,
          submit_confirmed: false,
          retryable: false,
          auto_retry_allowed: false,
          result_class: "RESULT_UNKNOWN",
          status: "result_unknown",
          host_submit_receipt: submit?.host_submit_receipt ?? null,
          submit_diagnostics: submit?.submit_diagnostics ?? null
        });
        return this.#storeTerminal(wakeId, receipt);
      }

      let after;
      try {
        after = validateMachineObservation(await this.driver.observeTarget(verified.browser_target_id));
      } catch {
        receipt = this.#signedReceipt({
          wakeId,
          nonce,
          verified,
          payload,
          dispatch_attempted: true,
          submit_confirmed: false,
          retryable: false,
          auto_retry_allowed: false,
          result_class: "RESULT_UNKNOWN",
          status: "result_unknown",
          host_submit_receipt: submit?.host_submit_receipt ?? null,
          submit_diagnostics: submit?.submit_diagnostics ?? null
        });
        return this.#storeTerminal(wakeId, receipt);
      }
      if (!sameTarget(after, verified)) {
        receipt = this.#signedReceipt({
          wakeId,
          nonce,
          verified,
          payload,
          dispatch_attempted: true,
          submit_confirmed: false,
          retryable: false,
          auto_retry_allowed: false,
          result_class: "RESULT_UNKNOWN",
          status: "result_unknown",
          host_submit_receipt: submit?.host_submit_receipt ?? null,
          submit_diagnostics: submit?.submit_diagnostics ?? null
        });
        return this.#storeTerminal(wakeId, receipt);
      }

      receipt = this.#signedReceipt({
        wakeId,
        nonce,
        verified,
        payload,
        dispatch_attempted: true,
        submit_confirmed: true,
        retryable: false,
        auto_retry_allowed: false,
        result_class: "SUBMIT_CONFIRMED",
        status: "submit_confirmed",
        host_submit_receipt: submit?.host_submit_receipt ?? null,
        submit_diagnostics: submit?.submit_diagnostics ?? null
      });
      return this.#storeTerminal(wakeId, receipt);
    } finally {
      this.activeWakeIds.delete(wakeId);
    }
  }

  #signedReceipt({ wakeId, nonce, wake_nonce_sha256, verified, payload, continuation_payload_sha256, dispatch_attempted, submit_confirmed, retryable, auto_retry_allowed, result_class, status, host_submit_receipt, submit_diagnostics }) {
    const receipt = {
      schema_version: 1,
      receipt_id: `wr_${randomUUID()}`,
      wake_id: wakeId,
      wake_nonce_sha256: wake_nonce_sha256 ?? sha256Text(nonEmpty(nonce, "wake_nonce")),
      conversation_id: verified.conversation_id,
      browser_target_id: verified.browser_target_id,
      target_generation: verified.target_generation,
      ownership_generation: verified.ownership_generation,
      continuation_payload_sha256: continuation_payload_sha256 ?? sha256Text(nonEmpty(payload, "continuation_payload")),
      dispatch_attempted: dispatch_attempted === true,
      submit_confirmed: submit_confirmed === true,
      retryable: retryable === true,
      auto_retry_allowed: auto_retry_allowed === true,
      result_class: nonEmpty(result_class, "result_class"),
      status: nonEmpty(status, "status"),
      host_submit_receipt: host_submit_receipt == null ? null : String(host_submit_receipt),
      submit_diagnostics: sanitizeSubmitDiagnostics(submit_diagnostics),
      observed_at_ms: nowMs(this.clock),
      provenance: REENTRY_RECEIPT_PROVENANCE
    };
    return { ...receipt, host_mac_sha256: hmac(this.secret, receipt) };
  }
}

export function verifyReentryReceipt(secret, receipt, {
  wake_id,
  wake_nonce,
  continuation_payload,
  conversation_id,
  browser_target_id,
  target_generation,
  ownership_generation
}) {
  if (Number(receipt?.schema_version) !== 1 || receipt?.schema_version !== 1) {
    throw new Error("re-entry receipt schema_version is unsupported");
  }
  const unsigned = { ...receipt };
  const mac = nonEmpty(unsigned.host_mac_sha256, "receipt.host_mac_sha256");
  delete unsigned.host_mac_sha256;
  if (!secureEqualHex(mac, hmac(secret, unsigned))) throw new Error("re-entry receipt authentication failed");
  if (receipt.provenance !== REENTRY_RECEIPT_PROVENANCE) throw new Error("untrusted re-entry receipt provenance");
  if (receipt.wake_id !== nonEmpty(wake_id, "expected wake_id")) throw new Error("wrong wake receipt");
  if (receipt.conversation_id !== nonEmpty(conversation_id, "expected conversation_id")) throw new Error("wrong Web conversation receipt");
  if (receipt.browser_target_id !== nonEmpty(browser_target_id, "expected browser_target_id")) throw new Error("wrong browser target receipt");
  const expectedTargetGeneration = positiveInteger(target_generation, "expected target_generation");
  const expectedOwnershipGeneration = positiveInteger(ownership_generation, "expected ownership_generation");
  if (receipt.target_generation !== expectedTargetGeneration) throw new Error("stale target generation receipt");
  if (receipt.ownership_generation !== expectedOwnershipGeneration) throw new Error("stale ownership generation receipt");
  positiveInteger(receipt.target_generation, "receipt.target_generation");
  positiveInteger(receipt.ownership_generation, "receipt.ownership_generation");
  if (receipt.wake_nonce_sha256 !== sha256Text(nonEmpty(wake_nonce, "expected wake_nonce"))) {
    throw new Error("wake nonce hash mismatch");
  }
  if (receipt.continuation_payload_sha256 !== sha256Text(nonEmpty(continuation_payload, "expected continuation_payload"))) {
    throw new Error("continuation payload hash mismatch");
  }
  nonEmpty(receipt.receipt_id, "receipt.receipt_id");
  positiveInteger(receipt.observed_at_ms, "receipt.observed_at_ms");

  const dispatchAttempted = receipt.dispatch_attempted;
  const submitConfirmed = receipt.submit_confirmed;
  const retryable = receipt.retryable;
  const autoRetryAllowed = receipt.auto_retry_allowed;
  const resultClass = receipt.result_class;
  if (![dispatchAttempted, submitConfirmed, retryable, autoRetryAllowed].every((value) => typeof value === "boolean")) {
    throw new Error("re-entry receipt result flags are invalid");
  }
  if (submitConfirmed && !dispatchAttempted) throw new Error("re-entry receipt confirms submit without dispatch");
  const diagnostics = receipt.submit_diagnostics;
  if (diagnostics != null) {
    if (!diagnostics || typeof diagnostics !== "object" || Array.isArray(diagnostics)) {
      throw new Error("re-entry receipt submit diagnostics are invalid");
    }
    const sanitized = sanitizeSubmitDiagnostics(diagnostics);
    if (canonicalJson(sanitized) !== canonicalJson(diagnostics)) {
      throw new Error("re-entry receipt submit diagnostics contain unsupported fields");
    }
    if (diagnostics.wake_id != null && diagnostics.wake_id !== receipt.wake_id) {
      throw new Error("re-entry receipt submit diagnostics wake mismatch");
    }
    if (diagnostics.browser_target_id != null && diagnostics.browser_target_id !== receipt.browser_target_id) {
      throw new Error("re-entry receipt submit diagnostics target mismatch");
    }
    if (typeof diagnostics.dispatch_attempted === "boolean" && diagnostics.dispatch_attempted !== dispatchAttempted) {
      throw new Error("re-entry receipt submit diagnostics dispatch mismatch");
    }
    if (typeof diagnostics.submit_confirmed === "boolean" && diagnostics.submit_confirmed !== submitConfirmed) {
      throw new Error("re-entry receipt submit diagnostics confirmation mismatch");
    }
  }
  if (resultClass === "SUBMIT_CONFIRMED") {
    if (!dispatchAttempted || !submitConfirmed || retryable || autoRetryAllowed) {
      throw new Error("re-entry receipt confirmed-submit state is inconsistent");
    }
  } else if (resultClass === "RESULT_UNKNOWN") {
    if (!dispatchAttempted || submitConfirmed || retryable || autoRetryAllowed) {
      throw new Error("re-entry receipt result-unknown state is inconsistent");
    }
  } else if (resultClass === "CONFIRMED_FAILURE_BEFORE_DISPATCH") {
    if (dispatchAttempted || submitConfirmed || autoRetryAllowed !== retryable) {
      throw new Error("re-entry receipt pre-dispatch failure state is inconsistent");
    }
  } else {
    throw new Error("re-entry receipt result_class is invalid");
  }
  return receipt;
}
