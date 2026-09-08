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
