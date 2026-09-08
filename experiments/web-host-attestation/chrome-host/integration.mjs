import { createRuntimeHostProvider } from "../provider.mjs";
import { verifyReentryReceipt } from "../attestation.mjs";
import { createChromeHostDriver } from "./client.mjs";
import { RUNTIME_HOST_REQUEST_TIMEOUT_MS } from "./extension/timeout-budget.js";

export function createChromeRuntimeHostIntegration(options) {
  const secret = options && options.secret;
  const socketPath = options && options.socketPath;
  const clock = options && options.clock || Date.now;
  const verifierStatePath = options && options.verifierStatePath || null;
  const reentryStatePath = options && options.reentryStatePath || null;
  const timeoutMs = options && options.timeoutMs || RUNTIME_HOST_REQUEST_TIMEOUT_MS;

  const driver = createChromeHostDriver({
    socketPath: socketPath,
    timeoutMs: timeoutMs
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
      "attestAndVerify",
      "consumeVerifiedTarget",
      "submitReentry",
      "submitAndVerifyReentry"
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
  if (operation !== "attest_and_verify" && operation !== "submit_reentry") {
    throw new Error("unsupported Runtime Host operation: " + operation);
  }
  const integration = options && options.integration;
  if (!integration) {
    throw new Error("Runtime Host integration is required");
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
