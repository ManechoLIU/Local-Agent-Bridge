import { createHash, createHmac, randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import { chmodSync, existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

export const ATTESTATION_PROVENANCE = "browser_host_machine_attested_v1";
export const MACHINE_OBSERVATION_SOURCE = "browser_cdp_page_frame_tree_v1";
export const VERIFIED_TARGET_PROVENANCE = "runtime_host_verifier_v1";
export const REENTRY_RECEIPT_PROVENANCE = "browser_host_reentry_receipt_v1";

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


function savePrivateState(path, value) {
  if (!path) return;
  const dir = dirname(path);
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  chmodSync(dir, 0o700);
  const temp = join(dir, `.${randomUUID()}.tmp`);
  writeFileSync(temp, JSON.stringify(value), { mode: 0o600, flag: "wx" });
  chmodSync(temp, 0o600);
  renameSync(temp, path);
  chmodSync(path, 0o600);
}

function loadPrivateState(path) {
  if (!path || !existsSync(path)) return null;
  return JSON.parse(readFileSync(path, "utf8"));
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
