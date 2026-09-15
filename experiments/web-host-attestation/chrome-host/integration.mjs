import { createRuntimeHostProvider } from "../provider.mjs";
import { verifyCurrentEntryEvidence, verifyReentryReceipt, verifyToolPreReceipt, verifyToolTerminalReceipt } from "../attestation.mjs";
import { createChromeHostDriver } from "./client.mjs";
import { RUNTIME_HOST_REQUEST_TIMEOUT_MS } from "./extension/timeout-budget.js";

export function createChromeRuntimeHostIntegration(options) {
  const secret = options && options.secret;
  const socketPath = options && options.socketPath;
  const clock = options && options.clock || Date.now;
  const verifierStatePath = options && options.verifierStatePath || null;
  const reentryStatePath = options && options.reentryStatePath || null;
  const timeoutMs = options && options.timeoutMs || RUNTIME_HOST_REQUEST_TIMEOUT_MS;
  const expectedExtensionBindingSha256 = options && options.expectedExtensionBindingSha256 || null;

  const driver = createChromeHostDriver({
    socketPath: socketPath,
    timeoutMs: timeoutMs,
    expectedExtensionBindingSha256: expectedExtensionBindingSha256
  });

  const provider = createRuntimeHostProvider({
    secret: secret,
    driver: driver,
    clock: clock,
    verifierStatePath: verifierStatePath,
    reentryStatePath: reentryStatePath
  });

  return Object.freeze({
    interface_version: 1,
    capabilities: Object.freeze([
      "issueChallenge",
      "requestAttestation",
      "verifyAttestation",
      "discoverCurrentEntry",
      "attestAndVerify",
      "consumeVerifiedTarget",
      "submitReentry",
      "submitAndVerifyReentry",
      "verifyToolPreReceipt",
      "verifyToolTerminalReceipt"
    ]),

    issueChallenge(input) {
      return provider.issueChallenge(input);
    },

    async requestAttestation(challenge) {
      return await driver.attest(challenge);
    },

    verifyAttestation(attestation, expected) {
      return provider.verifyAttestation(attestation, expected);
    },

    async discoverCurrentEntry(input) {
      const raw = await driver.discoverCurrentEntry(input);
      return verifyCurrentEntryEvidence({
        secret: secret,
        evidence: raw,
        expectedTargetGeneration: input && input.target_generation,
        expectedOwnershipGeneration: input && input.ownership_generation,
        clock: clock
      });
    },

    async verifyCurrentEntry(input) {
      const current = input && input.current_entry;
      const verifiedTarget = provider.verifyCurrentEntry(current, {
        conversation_id: input && input.conversation_id,
        target_generation: input && input.target_generation,
        ownership_generation: input && input.ownership_generation
      });
      return Object.freeze({
        host_receipt_id: current.host_receipt_id,
        verified_target: verifiedTarget
      });
    },

    async verifyToolPreReceipt(input) {
      return await verifyToolReceiptWithCurrentEntry(input, verifyToolPreReceipt);
    },

    async verifyToolTerminalReceipt(input) {
      return await verifyToolReceiptWithCurrentEntry(input, verifyToolTerminalReceipt);
    },

    async attestAndVerify(input) {
      const challenge = provider.issueChallenge(input);
      const attestation = await driver.attest(challenge);
      const verifiedTarget = provider.verifyAttestation(attestation, input);
      return Object.freeze({
        challenge_id: challenge.challenge_id,
        host_receipt_id: attestation.host_receipt_id,
        verified_target: verifiedTarget
      });
    },

    consumeVerifiedTarget(hostReceiptId, expected) {
      return provider.consumeVerifiedTarget(hostReceiptId, expected);
    },

    async submitReentry(input) {
      return await provider.submitReentry(input);
    },

    async submitAndVerifyReentry(input) {
      const verifiedTarget = provider.consumeVerifiedTarget(input && input.host_receipt_id, {
        conversation_id: input && input.conversation_id,
        target_generation: input && input.expected_target_generation,
        ownership_generation: input && input.expected_ownership_generation
      });
      const receipt = await provider.submitReentry({
        verified_target: verifiedTarget,
        wake_id: input && input.wake_id,
        wake_nonce: input && input.wake_nonce,
        continuation_payload: input && input.continuation_payload,
        expected_target_generation: input && input.expected_target_generation,
        expected_ownership_generation: input && input.expected_ownership_generation
      });
      return verifyReentryReceipt(secret, receipt, {
        wake_id: input && input.wake_id,
        wake_nonce: input && input.wake_nonce,
        continuation_payload: input && input.continuation_payload,
        conversation_id: input && input.conversation_id,
        browser_target_id: verifiedTarget.browser_target_id,
        target_generation: input && input.expected_target_generation,
        ownership_generation: input && input.expected_ownership_generation
      });
    }
  });

  async function verifyToolReceiptWithCurrentEntry(input, verifyReceipt) {
    const fresh = await driver.discoverCurrentEntryWithEpoch({
      target_generation: input.expected_target_generation,
      ownership_generation: input.expected_ownership_generation
    });
    const current = verifyCurrentEntryEvidence({ secret, evidence: fresh.current_entry,
      expectedTargetGeneration: input.expected_target_generation,
      expectedOwnershipGeneration: input.expected_ownership_generation, clock });
    const receipt = verifyReceipt({ ...input, secret, clock });
    for (const field of ["conversation_id", "browser_target_id", "top_frame_id", "loader_id", "secure_origin", "generation_anchor_sha256"]) {
      if (receipt[field] !== current[field]) throw new Error("tool receipt current-entry " + field + " mismatch");
    }
    if (receipt.extension_binding_sha256 !== fresh.extension_epoch.binding_sha256 ||
        receipt.extension_instance_id_sha256 !== fresh.extension_epoch.instance_id_sha256 ||
        receipt.extension_loaded_at_ms !== fresh.extension_epoch.loaded_at_ms) throw new Error("tool receipt extension epoch mismatch");
    return Object.freeze({ receipt, current_entry: current });
  }
}

function requiredRuntimeText(value, name, max) {
  const text = String(value == null ? "" : value).trim();
  if (!text) throw new Error(name + " is required");
  if (text.length > (max || 256)) throw new Error(name + " exceeds limit");
  return text;
}

function requiredRuntimeGeneration(value, name) {
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < 1) throw new Error(name + " must be a positive integer");
  return number;
}

export async function executeRuntimeVerifierCommand(input, options) {
  const operation = requiredRuntimeText(input && input.operation, "operation", 64);
  if (!["discover_current_entry", "verify_current_entry", "attest_and_verify", "submit_reentry", "verify_tool_pre", "verify_tool_terminal"].includes(operation)) {
    throw new Error("unsupported Runtime Host operation: " + operation);
  }
  const integration = options && options.integration;
  if (!integration) {
    throw new Error("Runtime Host integration is required");
  }

  if (operation === "verify_tool_pre" || operation === "verify_tool_terminal") {
    const expectedKeys = operation === "verify_tool_pre"
      ? ["protocol", "operation", "receipt_sha256", "receipt", "expected_target_generation", "expected_ownership_generation", "expected_request_digests"]
      : ["protocol", "operation", "receipt_sha256", "receipt", "expected_target_generation", "expected_ownership_generation", "expected_request_digests", "expected_pre_chain"];
    const actual = input && typeof input === "object" && !Array.isArray(input) ? Object.keys(input).sort() : [];
    if (actual.length !== expectedKeys.length || actual.some((key) => !expectedKeys.includes(key))) throw new Error("v2 Runtime Host command keys are invalid");
    if (input.protocol !== "runtime_host_verifier_cli_v2") throw new Error("v2 Runtime Host protocol is required");
    const request = {
      receipt: input.receipt,
      receipt_sha256: requiredRuntimeText(input.receipt_sha256, "receipt_sha256", 64),
      expected_target_generation: requiredRuntimeGeneration(input.expected_target_generation, "expected_target_generation"),
      expected_ownership_generation: requiredRuntimeGeneration(input.expected_ownership_generation, "expected_ownership_generation"),
      expected_request_digests: input.expected_request_digests,
      ...(operation === "verify_tool_terminal" ? { expected_pre_chain: input.expected_pre_chain } : {})
    };
    const result = await integration[operation === "verify_tool_pre" ? "verifyToolPreReceipt" : "verifyToolTerminalReceipt"](request);
    const receipt = result.receipt;
    return Object.freeze({ ok: true, protocol: "runtime_host_verifier_cli_v2", operation,
      receipt_sha256: request.receipt_sha256,
      receipt_id: operation === "verify_tool_pre" ? receipt.pre_receipt_id : receipt.terminal_receipt_id,
      capability_id: receipt.capability_id, host_tool_execution_id: receipt.host_tool_execution_id,
      current_entry: result.current_entry,
      ...(operation === "verify_tool_terminal" ? { pre_chain: Object.freeze({ pre_receipt_id: receipt.pre_receipt_id, pre_receipt_sha256: receipt.pre_receipt_sha256 }) } : {}) });
  }

  if (operation === "discover_current_entry") {
    if (Object.prototype.hasOwnProperty.call(input || {}, "conversation_id")) {
      throw new Error("current-entry discovery does not accept caller conversation identity");
    }
    if (typeof integration.discoverCurrentEntry !== "function") {
      throw new Error("Runtime Host current-entry integration is required");
    }
    const request = {
      target_generation: requiredRuntimeGeneration(input && input.target_generation, "target_generation"),
      ownership_generation: requiredRuntimeGeneration(input && input.ownership_generation, "ownership_generation")
    };
    const current = await integration.discoverCurrentEntry(request);
    if (!current || current.provenance !== "runtime_host_current_entry_v1" ||
        current.entry_scope !== "runtime_invocation" ||
        current.machine_source !== "host_invocation_context_v1") {
      throw new Error("Runtime Host current-entry discovery did not return trusted machine evidence");
    }
    if (current.target_generation !== request.target_generation ||
        current.ownership_generation !== request.ownership_generation) {
      throw new Error("Runtime Host current-entry discovery returned stale ownership fence");
    }
    if (current.secure_origin !== "https://chatgpt.com") {
      throw new Error("Runtime Host current-entry discovery returned invalid secure origin");
    }
    for (const field of ["conversation_id", "browser_target_id", "top_frame_id", "loader_id", "host_receipt_id"]) {
      requiredRuntimeText(current[field], "current_entry." + field, 512);
    }
    requiredRuntimeGeneration(current.observed_at_unix_ms, "current_entry.observed_at_unix_ms");
    return Object.freeze({ ok: true, operation: operation, current_entry: current });
  }

  if (operation === "verify_current_entry") {
    if (typeof integration.verifyCurrentEntry !== "function") {
      throw new Error("Runtime Host current-entry verification integration is required");
    }
    const request = {
      conversation_id: requiredRuntimeText(input && input.conversation_id, "conversation_id", 128),
      target_generation: requiredRuntimeGeneration(input && input.target_generation, "target_generation"),
      ownership_generation: requiredRuntimeGeneration(input && input.ownership_generation, "ownership_generation"),
      current_entry: input && input.current_entry
    };
    const result = await integration.verifyCurrentEntry(request);
    const verified = result && result.verified_target;
    const receipt = result && result.host_receipt_id;
    if (!verified || verified.provenance !== "runtime_host_verifier_v1") {
      throw new Error("Runtime Host current-entry verifier did not return a trusted verified target");
    }
    if (verified.conversation_id !== request.conversation_id ||
        verified.target_generation !== request.target_generation ||
        verified.ownership_generation !== request.ownership_generation) {
      throw new Error("Runtime Host current-entry verified target does not match requested ownership fence");
    }
    return Object.freeze({
      ok: true, operation,
      host_receipt_id: requiredRuntimeText(receipt, "host_receipt_id", 256),
      verified_target: verified
    });
  }

  if (operation === "attest_and_verify") {
    if (typeof integration.attestAndVerify !== "function") {
      throw new Error("Runtime Host attestation integration is required");
    }
    const request = {
      conversation_id: requiredRuntimeText(input && input.conversation_id, "conversation_id", 128),
      target_generation: requiredRuntimeGeneration(input && input.target_generation, "target_generation"),
      ownership_generation: requiredRuntimeGeneration(input && input.ownership_generation, "ownership_generation")
    };
    const result = await integration.attestAndVerify(request);
    const verified = result && result.verified_target;
    if (!verified || verified.provenance !== "runtime_host_verifier_v1") {
      throw new Error("Runtime Host verifier did not return a trusted verified target");
    }
    if (verified.conversation_id !== request.conversation_id ||
        verified.target_generation !== request.target_generation ||
        verified.ownership_generation !== request.ownership_generation) {
      throw new Error("Runtime Host verified target does not match requested ownership fence");
    }
    return Object.freeze({
      ok: true,
      operation: operation,
      challenge_id: requiredRuntimeText(result.challenge_id, "challenge_id", 256),
      host_receipt_id: requiredRuntimeText(result.host_receipt_id, "host_receipt_id", 256),
      verified_target: verified
    });
  }

  if (operation === "submit_reentry") {
    if (typeof integration.submitAndVerifyReentry !== "function") {
      throw new Error("Runtime Host re-entry integration is required");
    }
    const expectedTargetGeneration = requiredRuntimeGeneration(
      input && input.expected_target_generation, "expected_target_generation"
    );
    const expectedOwnershipGeneration = requiredRuntimeGeneration(
      input && input.expected_ownership_generation, "expected_ownership_generation"
    );
    const conversationId = requiredRuntimeText(
      input && input.conversation_id, "conversation_id", 128
    );
    const request = {
      host_receipt_id: requiredRuntimeText(input && input.host_receipt_id, "host_receipt_id", 256),
      conversation_id: conversationId,
      wake_id: requiredRuntimeText(input && input.wake_id, "wake_id", 256),
      wake_nonce: requiredRuntimeText(input && input.wake_nonce, "wake_nonce", 256),
      continuation_payload: requiredRuntimeText(
        input && input.continuation_payload, "continuation_payload", 8000
      ),
      expected_target_generation: expectedTargetGeneration,
      expected_ownership_generation: expectedOwnershipGeneration
    };
    const receipt = await integration.submitAndVerifyReentry(request);
    return Object.freeze({
      ok: true,
      operation: operation,
      reentry_receipt: receipt
    });
  }
}
