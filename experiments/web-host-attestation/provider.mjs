import { RuntimeHostVerifier, WebReentryAdapter } from "./attestation.mjs";

/** Stable provider boundary for a future Runtime integration.
 * Runtime retains lifecycle, wake scheduling and retry policy ownership.
 */
export function createRuntimeHostProvider({ secret, driver, clock = Date.now, verifierStatePath = null, reentryStatePath = null, sleep = null } = {}) {
  const verifier = new RuntimeHostVerifier(secret, { clock, statePath: verifierStatePath });
  const reentry = new WebReentryAdapter(secret, driver, { clock, statePath: reentryStatePath, sleep });
  const supportsReconciliation = typeof driver?.reconcileContinuation === "function";
  const provider = {
    interface_version: 1,
    capabilities: Object.freeze([
      "issueChallenge",
      "verifyAttestation",
      "verifyCurrentEntry",
      "submitReentry",
      ...(supportsReconciliation ? ["reconcileReentry"] : [])
    ]),
    issueChallenge(input) {
      return verifier.issueChallenge(input);
    },
    verifyAttestation(attestation, expected) {
      return verifier.verifyAndConsume(attestation, expected);
    },
    verifyCurrentEntry(evidence, expected) {
      return verifier.verifyCurrentEntryAndRegister(evidence, expected);
    },
    consumeVerifiedTarget(hostReceiptId, expected) {
      return verifier.consumeVerifiedTarget(hostReceiptId, expected);
    },
    async submitReentry(input) {
      return reentry.reenter(input);
    }
  };
  if (supportsReconciliation) {
    provider.reconcileReentry = async function (input) {
      return reentry.reconcile(input);
    };
  }
  return Object.freeze(provider);
}
