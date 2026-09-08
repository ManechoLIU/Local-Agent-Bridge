import { createRuntimeHostProvider } from "../provider.mjs";
import { createChromeHostDriver } from "./client.mjs";

export function createChromeRuntimeHostIntegration(options) {
  const secret = options && options.secret;
  const socketPath = options && options.socketPath;
  const clock = options && options.clock || Date.now;
  const verifierStatePath = options && options.verifierStatePath || null;
  const reentryStatePath = options && options.reentryStatePath || null;
  const timeoutMs = options && options.timeoutMs || 5000;

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
      "submitReentry"
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

    async submitReentry(input) {
      return await provider.submitReentry(input);
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
  if (operation !== "attest_and_verify") {
    throw new Error("unsupported Runtime Host operation: " + operation);
  }
  const integration = options && options.integration;
  if (!integration || typeof integration.attestAndVerify !== "function") {
    throw new Error("Runtime Host integration is required");
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
