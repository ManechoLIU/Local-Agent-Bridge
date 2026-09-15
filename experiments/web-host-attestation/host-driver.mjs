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

function requiredPositiveInteger(value, name) {
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new Error(`${name} must be a positive integer`);
  }
  return value;
}

function optionalText(value, name) {
  if (value == null) return null;
  return requiredText(value, name);
}

function normalizeSubmitDiagnostics(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) return null;
  const debuggerAttach = input.debugger_attach && typeof input.debugger_attach === "object"
    ? input.debugger_attach : {};
  const composer = input.composer_preparation && typeof input.composer_preparation === "object"
    ? input.composer_preparation : {};
  const sendControl = input.send_control_resolution && typeof input.send_control_resolution === "object"
    ? input.send_control_resolution : {};
  const trusted = input.trusted_dispatch && typeof input.trusted_dispatch === "object"
    ? input.trusted_dispatch : {};
  const confirmation = input.confirmation && typeof input.confirmation === "object"
    ? input.confirmation : {};
  const foreground = input.foreground && typeof input.foreground === "object"
    ? input.foreground : {};
  return Object.freeze({
    schema_version: 1,
    wake_id: optionalText(input.wake_id, "submit diagnostics wake_id"),
    browser_target_id: optionalText(input.browser_target_id, "submit diagnostics browser_target_id"),
    phase: optionalText(input.phase, "submit diagnostics phase"),
    debugger_attach: Object.freeze({
      attempted: debuggerAttach.attempted === true,
      ok: debuggerAttach.ok === true
    }),
    composer_preparation: Object.freeze({
      attempted: composer.attempted === true,
      ok: composer.ok === true,
      status: optionalText(composer.status, "submit diagnostics composer status"),
      rollback_attempted: composer.rollback_attempted === true,
      rollback_succeeded: composer.rollback_succeeded === true
    }),
    send_control_resolution: Object.freeze({
      attempted: sendControl.attempted === true,
      ok: sendControl.ok === true,
      selector_kind: optionalText(sendControl.selector_kind, "submit diagnostics selector_kind"),
      coordinates_changed_since_prepare: sendControl.coordinates_changed_since_prepare === true
    }),
    trusted_dispatch: Object.freeze({
      attempted: trusted.attempted === true,
      focus_emulation: trusted.focus_emulation === true,
      mouse_moved: trusted.mouse_moved === true,
      mouse_pressed: trusted.mouse_pressed === true,
      mouse_released: trusted.mouse_released === true
    }),
    confirmation: Object.freeze({
      attempted: confirmation.attempted === true,
      ok: confirmation.ok === true,
      composer_empty: typeof confirmation.composer_empty === "boolean"
        ? confirmation.composer_empty : null,
      exact_new_user_message: typeof confirmation.exact_new_user_message === "boolean"
        ? confirmation.exact_new_user_message : null
    }),
    foreground: Object.freeze({
      mode: optionalText(foreground.mode, "submit diagnostics foreground mode"),
      activated_target: foreground.activated_target === true,
      restored_previous_active: foreground.restored_previous_active === true,
      user_changed_focus: foreground.user_changed_focus === true
    }),
    failure_stage: optionalText(input.failure_stage, "submit diagnostics failure_stage"),
    dispatch_attempted: input.dispatch_attempted === true,
    submit_confirmed: input.submit_confirmed === true,
    retryable: input.retryable === true
  });
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
function optionalBoolean(value) {
  return typeof value === "boolean" ? value : null;
}

function optionalNonNegativeInteger(value) {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

function normalizeReconciliationEvidence(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new Error("machine boundary reconciliation evidence is required");
  }
  return {
    status: requiredText(input.status, "reconciliation status"),
    journal_phase: optionalText(input.journal_phase, "reconciliation journal_phase"),
    journal_binding_matched: optionalBoolean(input.journal_binding_matched),
    conversation_matches: optionalBoolean(input.conversation_matches),
    target_matches: optionalBoolean(input.target_matches),
    composer_exact_payload: optionalBoolean(input.composer_exact_payload),
    composer_empty: optionalBoolean(input.composer_empty),
    exact_user_message_present: optionalBoolean(input.exact_user_message_present),
    stable_not_delivered: optionalBoolean(input.stable_not_delivered),
    baseline_user_message_count: optionalNonNegativeInteger(input.baseline_user_message_count)
  };
}

export function createHostDriver(machineBoundary) {
  const observe = requiredMethod(machineBoundary, "observeTarget");
  const submit = requiredMethod(machineBoundary, "submitContinuation");
  const reconcile = typeof machineBoundary?.reconcileContinuation === "function"
    ? machineBoundary.reconcileContinuation.bind(machineBoundary)
    : null;

  const driver = {
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
        submit_diagnostics: normalizeSubmitDiagnostics(receipt.submit_diagnostics)
      };
    }
  };
  if (reconcile) {
    driver.reconcileContinuation = async function (input) {
      const request = {
        browser_target_id: requiredText(input?.browser_target_id, "browser_target_id"),
        conversation_id: requiredText(input?.conversation_id, "conversation_id"),
        payload_sha256: requiredText(input?.payload_sha256, "payload_sha256"),
        wake_id: requiredText(input?.wake_id, "wake_id"),
        target_generation: requiredPositiveInteger(input?.target_generation, "target_generation"),
        ownership_generation: requiredPositiveInteger(input?.ownership_generation, "ownership_generation")
      };
      return normalizeReconciliationEvidence(await reconcile(request));
    };
  }
  return Object.freeze(driver);
}
