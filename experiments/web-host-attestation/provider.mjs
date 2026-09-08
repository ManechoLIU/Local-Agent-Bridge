import { RuntimeHostVerifier, WebReentryAdapter } from "./attestation.mjs";

/** Stable provider boundary for a future Runtime integration.
 * Runtime retains lifecycle, wake scheduling and retry policy ownership.
 */
export function createRuntimeHostProvider({ secret, driver, clock = Date.now, verifierStatePath = null, reentryStatePath = null } = {}) {
  const verifier = new RuntimeHostVerifier(secret, { clock, statePath: verifierStatePath });
  const reentry = new WebReentryAdapter(secret, driver, { clock, statePath: reentryStatePath });
  return Object.freeze({
    interface_version: 1,
    capabilities: Object.freeze(["issueChallenge", "verifyAttestation", "submitReentry"]),
    issueChallenge(input) {
      return verifier.issueChallenge(input);
    },
    verifyAttestation(attestation, expected) {
      return verifier.verifyAndConsume(attestation, expected);
    },
    consumeVerifiedTarget(hostReceiptId, expected) {
      return verifier.consumeVerifiedTarget(hostReceiptId, expected);
    },
    async submitReentry(input) {
      return reentry.reenter(input);
    }
  });
}
