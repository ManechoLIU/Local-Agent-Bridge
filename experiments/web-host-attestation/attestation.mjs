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
  constructor(secret, { clock = Date.now, challengeTtlMs = 30_000, attestationSkewMs = 5_000, statePath = null } = {}) {
    if (!secret || Buffer.byteLength(secret) < 32) throw new Error("verifier secret must be at least 32 bytes");
    this.secret = secret;
    this.clock = clock;
    this.challengeTtlMs = challengeTtlMs;
    this.attestationSkewMs = attestationSkewMs;
    this.statePath = statePath;
    const persisted = loadPrivateState(statePath);
    this.challenges = new Map(
      Array.isArray(persisted?.challenges)
        ? persisted.challenges.map(([id, record]) => [id, record])
        : []
    );
    this.usedAttestationReceipts = new Set(persisted?.used_attestation_receipts ?? []);
  }

  #persist() {
    savePrivateState(this.statePath, {
      schema_version: 1,
      challenges: [...this.challenges.entries()],
      used_attestation_receipts: [...this.usedAttestationReceipts]
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
    this.#persist();
    const verified = {
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
    };
    return verified;
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
    this.usedWakeIds = new Set(persisted?.used_wake_ids ?? []);
  }

  #persist() {
    savePrivateState(this.statePath, {
      schema_version: 1,
      used_wake_ids: [...this.usedWakeIds]
    });
  }

  async reenter({ verified_target, wake_id, wake_nonce, continuation_payload, expected_target_generation, expected_ownership_generation }) {
    const wakeId = nonEmpty(wake_id, "wake_id");
    if (this.usedWakeIds.has(wakeId)) throw new Error("wake_id replayed");
    const verified = verified_target;
    if (verified?.provenance !== VERIFIED_TARGET_PROVENANCE) throw new Error("re-entry requires a verified target from Runtime Host verifier");
    if (verified.target_generation !== positiveInteger(expected_target_generation, "expected_target_generation")) throw new Error("stale target generation before re-entry");
    if (verified.ownership_generation !== positiveInteger(expected_ownership_generation, "expected_ownership_generation")) throw new Error("stale ownership generation before re-entry");
    const payload = nonEmpty(continuation_payload, "continuation_payload");
    const nonce = nonEmpty(wake_nonce, "wake_nonce");

    const pre = validateMachineObservation(await this.driver.observeTarget(verified.browser_target_id));
    if (!sameTarget(pre, verified)) throw new Error("Web target changed before re-entry");
    await this.driver.focusTarget(verified.browser_target_id);
    const focused = validateMachineObservation(await this.driver.observeTarget(verified.browser_target_id));
    if (!sameTarget(focused, verified)) throw new Error("Web target changed after focus and before submit");

    this.usedWakeIds.add(wakeId);
    this.#persist();
    let submit;
    try {
      submit = await this.driver.submitContinuation({
        browser_target_id: verified.browser_target_id,
        conversation_id: verified.conversation_id,
        payload,
        wake_id: wakeId,
        wake_nonce: nonce
      });
    } catch (error) {
      return this.#signedReceipt({
        wakeId,
        nonce,
        verified,
        payload,
        dispatch_attempted: true,
        submit_confirmed: false,
        retryable: Boolean(error?.retryable),
        status: error?.retryable ? "transient_unavailable" : "submit_failed",
        host_submit_receipt: null
      });
    }

    const after = validateMachineObservation(await this.driver.observeTarget(verified.browser_target_id));
    if (!sameTarget(after, verified)) {
      return this.#signedReceipt({
        wakeId,
        nonce,
        verified,
        payload,
        dispatch_attempted: true,
        submit_confirmed: false,
        retryable: false,
        status: "target_changed_after_submit",
        host_submit_receipt: submit?.host_submit_receipt ?? null
      });
    }
    return this.#signedReceipt({
      wakeId,
      nonce,
      verified,
      payload,
      dispatch_attempted: Boolean(submit?.dispatch_attempted),
      submit_confirmed: Boolean(submit?.submit_confirmed),
      retryable: Boolean(submit?.retryable),
      status: submit?.submit_confirmed ? "submit_confirmed" : submit?.status || "submit_unconfirmed",
      host_submit_receipt: submit?.host_submit_receipt ?? null
    });
  }

  #signedReceipt({ wakeId, nonce, verified, payload, dispatch_attempted, submit_confirmed, retryable, status, host_submit_receipt }) {
    const receipt = {
      schema_version: 1,
      receipt_id: `wr_${randomUUID()}`,
      wake_id: wakeId,
      wake_nonce_sha256: sha256Text(nonce),
      conversation_id: verified.conversation_id,
      browser_target_id: verified.browser_target_id,
      target_generation: verified.target_generation,
      ownership_generation: verified.ownership_generation,
      continuation_payload_sha256: sha256Text(payload),
      dispatch_attempted,
      submit_confirmed,
      retryable,
      status,
      host_submit_receipt,
      observed_at_ms: nowMs(this.clock),
      provenance: REENTRY_RECEIPT_PROVENANCE
    };
    return { ...receipt, host_mac_sha256: hmac(this.secret, receipt) };
  }
}

export function verifyReentryReceipt(secret, receipt, { wake_id, conversation_id, target_generation, ownership_generation }) {
  const unsigned = { ...receipt };
  const mac = nonEmpty(unsigned.host_mac_sha256, "receipt.host_mac_sha256");
  delete unsigned.host_mac_sha256;
  if (!secureEqualHex(mac, hmac(secret, unsigned))) throw new Error("re-entry receipt authentication failed");
  if (receipt.provenance !== REENTRY_RECEIPT_PROVENANCE) throw new Error("untrusted re-entry receipt provenance");
  if (receipt.wake_id !== wake_id) throw new Error("wrong wake receipt");
  if (receipt.conversation_id !== conversation_id) throw new Error("wrong Web conversation receipt");
  if (receipt.target_generation !== target_generation) throw new Error("stale target generation receipt");
  if (receipt.ownership_generation !== ownership_generation) throw new Error("stale ownership generation receipt");
  return receipt;
}
