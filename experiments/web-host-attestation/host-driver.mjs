import { machineObservationFromFrameTree, validateMachineObservation } from "./attestation.mjs";

function requiredText(value, name) {
  const text = String(value ?? "").trim();
  if (!text) throw new Error(`${name} is required`);
  return text;
}

function requiredMethod(boundary, name) {
  if (typeof boundary?.[name] !== "function") throw new Error(`machine boundary ${name} method is required`);
  return boundary[name].bind(boundary);
}

function normalizeObservation(raw, requestedTargetId) {
  const observation = raw?.frameTree
    ? machineObservationFromFrameTree({
        tab_id: raw.tab_id ?? requestedTargetId,
        frameTree: raw.frameTree,
        observed_at_ms: raw.observed_at_ms
      })
    : validateMachineObservation(raw);
  if (observation.browser_target_id !== requestedTargetId) {
    throw new Error("machine boundary returned a different browser target");
  }
  return observation;
}

/**
 * Adapts a real browser/Host machine boundary to the stable LAB Host driver.
 * DOM, title and x-openai headers are intentionally absent from this contract.
 */
export function createHostDriver(machineBoundary) {
  const observe = requiredMethod(machineBoundary, "observeTarget");
  const submit = requiredMethod(machineBoundary, "submitContinuation");

  return Object.freeze({
    interface_version: 1,
    async observeTarget(browserTargetId) {
      const target = requiredText(browserTargetId, "browser_target_id");
      return normalizeObservation(await observe(target), target);
    },
    async submitContinuation(input) {
      const request = {
        browser_target_id: requiredText(input?.browser_target_id, "browser_target_id"),
        conversation_id: requiredText(input?.conversation_id, "conversation_id"),
        payload: requiredText(input?.payload, "payload"),
        wake_id: requiredText(input?.wake_id, "wake_id"),
        wake_nonce: requiredText(input?.wake_nonce, "wake_nonce"),
        target_generation: Number(input?.target_generation),
        ownership_generation: Number(input?.ownership_generation)
      };
      const receipt = await submit(request);
      if (!receipt || typeof receipt !== "object") throw new Error("machine boundary submit receipt is required");
      if (typeof receipt.dispatch_attempted !== "boolean") throw new Error("submit receipt dispatch_attempted must be boolean");
      if (typeof receipt.submit_confirmed !== "boolean") throw new Error("submit receipt submit_confirmed must be boolean");
      if (receipt.submit_confirmed && !receipt.dispatch_attempted) throw new Error("submit cannot be confirmed before dispatch");
      return {
        dispatch_attempted: receipt.dispatch_attempted,
        submit_confirmed: receipt.submit_confirmed,
        retryable: Boolean(receipt.retryable),
        status: receipt.status == null ? null : requiredText(receipt.status, "submit receipt status"),
        host_submit_receipt: receipt.host_submit_receipt == null ? null : requiredText(receipt.host_submit_receipt, "host_submit_receipt"),
        submit_diagnostics: receipt.submit_diagnostics && typeof receipt.submit_diagnostics === "object"
          ? receipt.submit_diagnostics
          : null
      };
    }
  });
}
