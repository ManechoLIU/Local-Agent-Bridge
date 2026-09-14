import { randomUUID } from "node:crypto";
import {
  signCurrentEntryEvidence,
  signHostAttestation,
  ToolInvocationReceiptJournal,
  validateMachineObservation
} from "../attestation.mjs";

export const MAX_RUNTIME_MESSAGE_BYTES = 64 * 1024;
export const MAX_CONTINUATION_CHARS = 8000;

function requiredText(value, name, max) {
  const limit = max == null ? 8192 : max;
  const text = String(value == null ? "" : value).trim();
  if (!text) throw new Error(name + " is required");
  if (text.length > limit) throw new Error(name + " exceeds limit");
  return text;
}

function positiveInteger(value, name) {
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < 1) {
    throw new Error(name + " must be a positive integer");
  }
  return number;
}



function optionalText(value, name, max) {
  if (value == null) return null;
  return requiredText(value, name, max);
}

function normalizeSubmitDiagnostics(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) return null;
  const debuggerAttach = input.debugger_attach && typeof input.debugger_attach === "object"
    ? input.debugger_attach
    : {};
  const composer = input.composer_preparation && typeof input.composer_preparation === "object"
    ? input.composer_preparation
    : {};
  const sendControl = input.send_control_resolution && typeof input.send_control_resolution === "object"
    ? input.send_control_resolution
    : {};
  const trusted = input.trusted_dispatch && typeof input.trusted_dispatch === "object"
    ? input.trusted_dispatch
    : {};
  const confirmation = input.confirmation && typeof input.confirmation === "object"
    ? input.confirmation
    : {};
  const foreground = input.foreground && typeof input.foreground === "object"
    ? input.foreground
    : {};
  return Object.freeze({
    schema_version: 1,
    wake_id: optionalText(input.wake_id, "submit diagnostics wake_id", 256),
    browser_target_id: optionalText(input.browser_target_id, "submit diagnostics browser_target_id", 256),
    phase: optionalText(input.phase, "submit diagnostics phase", 128),
    debugger_attach: Object.freeze({
      attempted: debuggerAttach.attempted === true,
      ok: debuggerAttach.ok === true
    }),
    composer_preparation: Object.freeze({
      attempted: composer.attempted === true,
      ok: composer.ok === true,
      status: optionalText(composer.status, "submit diagnostics composer status", 128),
      rollback_attempted: composer.rollback_attempted === true,
      rollback_succeeded: composer.rollback_succeeded === true
    }),
    send_control_resolution: Object.freeze({
      attempted: sendControl.attempted === true,
      ok: sendControl.ok === true,
      selector_kind: optionalText(sendControl.selector_kind, "submit diagnostics selector_kind", 128),
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
        ? confirmation.composer_empty
        : null,
      exact_new_user_message: typeof confirmation.exact_new_user_message === "boolean"
        ? confirmation.exact_new_user_message
        : null
    }),
    foreground: Object.freeze({
      mode: optionalText(foreground.mode, "submit diagnostics foreground mode", 128),
      activated_target: foreground.activated_target === true,
      restored_previous_active: foreground.restored_previous_active === true,
      user_changed_focus: foreground.user_changed_focus === true
    }),
    failure_stage: optionalText(input.failure_stage, "submit diagnostics failure_stage", 128),
    dispatch_attempted: input.dispatch_attempted === true,
    submit_confirmed: input.submit_confirmed === true,
    retryable: input.retryable === true
  });
}

export function validateChallenge(input) {
  return Object.freeze({
    schema_version: positiveInteger(input && input.schema_version || 1, "challenge.schema_version"),
    challenge_id: requiredText(input && input.challenge_id, "challenge.challenge_id", 256),
    challenge_nonce: requiredText(input && input.challenge_nonce, "challenge.challenge_nonce", 256),
    host_type: requiredText(input && input.host_type, "challenge.host_type", 64),
    conversation_id: requiredText(input && input.conversation_id, "challenge.conversation_id", 128),
    target_generation: positiveInteger(input && input.target_generation, "challenge.target_generation"),
    ownership_generation: positiveInteger(input && input.ownership_generation, "challenge.ownership_generation"),
    issued_at_ms: positiveInteger(input && input.issued_at_ms, "challenge.issued_at_ms"),
    expires_at_ms: positiveInteger(input && input.expires_at_ms, "challenge.expires_at_ms")
  });
}

function validateBrowserResponse(response, requestId) {
  if (!response || typeof response !== "object") throw new Error("browser machine response is required");
  if (response.request_id !== requestId) throw new Error("browser machine response request_id mismatch");
  if (response.ok !== true) {
    throw new Error(requiredText(response.error, "browser machine error", 1024));
  }
  return response.result;
}

function extensionEpochHealthSummary(epoch) {
  if (!epoch || typeof epoch !== "object" || Array.isArray(epoch)) return {};
  const instanceHash = typeof epoch.instance_id_sha256 === "string" && /^[0-9a-f]{64}$/.test(epoch.instance_id_sha256)
    ? epoch.instance_id_sha256
    : null;
  const bindingHash = typeof epoch.binding_sha256 === "string" && /^[0-9a-f]{64}$/.test(epoch.binding_sha256)
    ? epoch.binding_sha256
    : null;
  const loadedAt = Number.isSafeInteger(epoch.loaded_at_ms) && epoch.loaded_at_ms > 0
    ? epoch.loaded_at_ms
    : null;
  const extensionVersion = currentExtensionVersion(epoch.extension_version);
  const registryProtocol = epoch.current_entry_registry_protocol_version;
  if (!instanceHash || !bindingHash || !loadedAt || !extensionVersion || registryProtocol !== 2) return {};
  return {
    extension_ready: true,
    extension_instance_id_sha256: instanceHash,
    extension_loaded_at_ms: loadedAt,
    extension_binding_sha256: bindingHash,
    extension_version: extensionVersion,
    current_entry_registry_protocol_version: registryProtocol
  };
}

function currentExtensionVersion(value) {
  const text = typeof value === "string" ? value : "";
  const match = /^(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?$/.exec(text);
  if (!match) return null;
  const parts = match.slice(1).filter((part) => part != null).map(Number);
  if (!parts.every(Number.isSafeInteger)) return null;
  for (const [index, required] of [0, 1, 2].entries()) {
    if (parts[index] !== required) return parts[index] > required ? text : null;
  }
  return text;
}

function extensionEpochReceipt(epoch) {
  const summary = extensionEpochHealthSummary(epoch);
  if (summary.extension_ready !== true) {
    throw new Error("HOST_EXTENSION_NOT_READY: loaded extension epoch is invalid");
  }
  return Object.freeze({
    binding_sha256: summary.extension_binding_sha256,
    instance_id_sha256: summary.extension_instance_id_sha256,
    loaded_at_ms: summary.extension_loaded_at_ms,
    extension_version: summary.extension_version,
    current_entry_registry_protocol_version: summary.current_entry_registry_protocol_version
  });
}

function sameExtensionEpoch(left, right) {
  if (left == null || right == null) return left === right;
  return left.binding_sha256 === right.binding_sha256 &&
    left.instance_id_sha256 === right.instance_id_sha256 &&
    left.loaded_at_ms === right.loaded_at_ms &&
    left.extension_version === right.extension_version &&
    left.current_entry_registry_protocol_version === right.current_entry_registry_protocol_version;
}

export function createNativeHostHandler(options) {
  const secret = options && options.secret;
  const sendBrowserCommand = options && options.sendBrowserCommand;
  const clock = options && options.clock || Date.now;
  const getLiveValidation = options && options.getLiveValidation || function () { return null; };
  const recordLiveValidation = options && options.recordLiveValidation || function () {};
  const requireExtensionReady = options && options.requireExtensionReady || null;
  const receiptJournal = options && options.receiptJournal || new ToolInvocationReceiptJournal(secret, {
    clock: clock,
    statePath: options && options.receiptStatePath || null
  });
  if (!secret || Buffer.byteLength(secret) < 32) {
    throw new Error("Host attestation key must be at least 32 bytes");
  }
  if (typeof sendBrowserCommand !== "function") {
    throw new Error("sendBrowserCommand is required");
  }
  if (requireExtensionReady != null && typeof requireExtensionReady !== "function") {
    throw new Error("requireExtensionReady must be a function");
  }

  function receiptInput(request, machineContext, epoch) {
    if (!epoch) throw new Error("HOST_EXTENSION_NOT_READY: receipt operations require a loaded extension epoch");
    return {
      request_digest_profile: request.request_digest_profile,
      bridge_call_id: request.bridge_call_id,
      bridge_challenge_nonce: request.bridge_challenge_nonce,
      transport_binding_id: request.transport_binding_id,
      workspace: request.workspace,
      production_release_revision: request.production_release_revision,
      production_endpoint_generation: request.production_endpoint_generation,
      bridge_principal_id: request.bridge_principal_id,
      bridge_execution_session_id: request.bridge_execution_session_id,
      bridge_capability_id: request.bridge_capability_id,
      bridge_workspace_id: request.bridge_workspace_id,
      tool_name: request.tool_name,
      outer_request_sha256: request.outer_request_sha256,
      forwarded_request_sha256: request.forwarded_request_sha256,
      normalized_request_sha256: request.normalized_request_sha256,
      machine_context: {
        conversation_id: machineContext.conversation_id,
        browser_target_id: machineContext.browser_target_id,
        top_frame_id: machineContext.top_frame_id,
        loader_id: machineContext.loader_id,
        secure_origin: machineContext.secure_origin,
        generation_anchor_sha256: machineContext.generation_anchor_sha256,
        extension_binding_sha256: epoch.binding_sha256,
        extension_instance_id_sha256: epoch.instance_id_sha256,
        extension_loaded_at_ms: epoch.loaded_at_ms
      }
    };
  }

  function recheckExtensionEpoch(before) {
    const after = requireExtensionReady ? extensionEpochReceipt(requireExtensionReady()) : null;
    if (!sameExtensionEpoch(before, after)) {
      throw new Error("HOST_EXTENSION_EPOCH_CHANGED: extension epoch changed during browser request");
    }
    return after;
  }

  async function browser(op, fields) {
    const requestId = "hb_" + randomUUID();
    const response = await sendBrowserCommand(Object.assign({
      request_id: requestId,
      op: op
    }, fields || {}));
    return validateBrowserResponse(response, requestId);
  }

  return async function handleRuntimeRequest(request) {
    if (!request || typeof request !== "object" || Array.isArray(request)) {
      throw new Error("runtime request must be an object");
    }
    const op = requiredText(request.op, "op", 64);
    const extensionEpoch = requireExtensionReady ? requireExtensionReady() : null;
    const epochReceipt = extensionEpoch ? extensionEpochReceipt(extensionEpoch) : null;
    const epochFields = epochReceipt ? { extension_epoch: epochReceipt } : {};

    if (op === "health") {
      const live = getLiveValidation() || {};
      return {
        status: "ready",
        interface_version: 1,
        submit_implemented: true,
        submit_live_validated: live.submit_live_validated === true,
        ...(live.submit_live_validated === true && Number.isSafeInteger(live.validated_at_ms)
          ? { submit_live_validated_at_ms: live.validated_at_ms }
          : {}),
        ...extensionEpochHealthSummary(extensionEpoch),
        ...epochFields
      };
    }

    if (op === "discover_current_entry") {
      if (Object.prototype.hasOwnProperty.call(request, "conversation_id")) {
        throw new Error("current-entry discovery does not accept caller conversation identity");
      }
      const targetGeneration = positiveInteger(request.target_generation, "target_generation");
      const ownershipGeneration = positiveInteger(request.ownership_generation, "ownership_generation");
      const browserResult = await browser(op, {});
      const machineContext = browserResult && browserResult.current_entry_context;
      if (!machineContext) {
        throw new Error("HOST_CURRENT_ENTRY_UNAVAILABLE: trusted Host invocation context unavailable");
      }
      const verifiedEpochReceipt = recheckExtensionEpoch(epochReceipt);
      const verifiedEpochFields = verifiedEpochReceipt ? { extension_epoch: verifiedEpochReceipt } : {};
      return {
        current_entry: signCurrentEntryEvidence({
          secret: secret,
          machineContext: machineContext,
          targetGeneration: targetGeneration,
          ownershipGeneration: ownershipGeneration,
          clock: clock
        }),
        ...verifiedEpochFields
      };
    }

    if (op === "tool_receipt_issue" || op === "tool_receipt_consume") {
      if (Object.prototype.hasOwnProperty.call(request, "machine_context") ||
          Object.prototype.hasOwnProperty.call(request, "conversation_id") ||
          Object.prototype.hasOwnProperty.call(request, "browser_target_id")) {
        throw new Error("tool receipt operations do not accept caller machine identity");
      }
      const browserResult = await browser(op, {});
      const machineContext = browserResult && browserResult.current_entry_context;
      if (!machineContext) throw new Error("HOST_CURRENT_ENTRY_UNAVAILABLE: trusted Host invocation context unavailable");
      const verifiedEpochReceipt = recheckExtensionEpoch(epochReceipt);
      const verifiedEpochFields = verifiedEpochReceipt ? { extension_epoch: verifiedEpochReceipt } : {};
      const input = receiptInput(request, machineContext, verifiedEpochReceipt);
      if (op === "tool_receipt_issue") {
        return { capability: receiptJournal.issueCapability(input), ...verifiedEpochFields };
      }
      return {
        pre_receipt: receiptJournal.consumeCapability({
          ...input,
          capability: request.capability,
          retry_outer_request_sha256: request.retry_outer_request_sha256,
          host_tool_execution_id: request.host_tool_execution_id
        }),
        ...verifiedEpochFields
      };
    }

    if (op === "tool_receipt_terminal") {
      const pre = request.pre_receipt;
      if (!pre || typeof pre !== "object" || Array.isArray(pre)) {
        throw new Error("pre_receipt is required");
      }
      const input = receiptInput(request, {
        conversation_id: pre.conversation_id,
        browser_target_id: pre.browser_target_id,
        top_frame_id: pre.top_frame_id,
        loader_id: pre.loader_id,
        secure_origin: pre.secure_origin,
        generation_anchor_sha256: pre.generation_anchor_sha256
      }, epochReceipt);
      return {
        terminal_receipt: receiptJournal.terminalize({
          ...input,
          pre_receipt: pre,
          retry_outer_request_sha256: request.retry_outer_request_sha256,
          backend_route: request.backend_route,
          http_status: request.http_status,
          response_body_sha256: request.response_body_sha256,
          terminal_classification: request.terminal_classification
        }),
        ...epochFields
      };
    }

    if (op === "attest") {
      const challenge = validateChallenge(request.challenge);
      if (challenge.host_type !== "chatgpt_web") throw new Error("unsupported challenge host_type");
      const browserResult = await browser("observe_conversation", {
        conversation_id: challenge.conversation_id
      });
      const observation = validateMachineObservation(browserResult && browserResult.observation);
      if (observation.conversation_id !== challenge.conversation_id) {
        throw new Error("browser machine observation conversation mismatch");
      }
      return {
        attestation: signHostAttestation({
          secret: secret,
          challenge: challenge,
          observation: observation,
          clock: clock
        }),
        ...epochFields
      };
    }

    if (op === "observe_target") {
      const target = requiredText(request.browser_target_id, "browser_target_id", 256);
      const browserResult = await browser("observe_target", {
        browser_target_id: target
      });
      const observation = validateMachineObservation(browserResult && browserResult.observation);
      if (observation.browser_target_id !== target) {
        throw new Error("browser machine returned wrong target");
      }
      return { observation: observation, ...epochFields };
    }

    if (op === "submit_continuation") {
      const target = requiredText(request.browser_target_id, "browser_target_id", 256);
      const conversation = requiredText(request.conversation_id, "conversation_id", 128);
      const payload = requiredText(request.payload, "payload", MAX_CONTINUATION_CHARS);
      const wakeId = requiredText(request.wake_id, "wake_id", 256);
      const wakeNonce = requiredText(request.wake_nonce, "wake_nonce", 256);
      const targetGeneration = positiveInteger(request.target_generation, "target_generation");
      const ownershipGeneration = positiveInteger(request.ownership_generation, "ownership_generation");
      const result = await browser("submit_continuation", {
        browser_target_id: target,
        conversation_id: conversation,
        payload: payload,
        wake_id: wakeId,
        wake_nonce: wakeNonce,
        target_generation: targetGeneration,
        ownership_generation: ownershipGeneration
      });
      if (typeof (result && result.dispatch_attempted) !== "boolean" ||
          typeof (result && result.submit_confirmed) !== "boolean") {
        throw new Error("browser submit receipt flags are required");
      }
      if (result.submit_confirmed && !result.dispatch_attempted) {
        throw new Error("browser submit cannot be confirmed before dispatch");
      }
      const normalized = {
        dispatch_attempted: result.dispatch_attempted,
        submit_confirmed: result.submit_confirmed,
        retryable: Boolean(result.retryable),
        status: result.status == null ? null : requiredText(result.status, "submit status", 128),
        host_submit_receipt: result.host_submit_receipt == null
          ? null
          : requiredText(result.host_submit_receipt, "host_submit_receipt", 512),
        submit_diagnostics: normalizeSubmitDiagnostics(result.submit_diagnostics)
      };
      if (normalized.submit_confirmed) {
        recordLiveValidation({
          submit_live_validated: true,
          validated_at_ms: Number(clock()),
          conversation_id: conversation,
          browser_target_id: target,
          host_submit_receipt: normalized.host_submit_receipt
        });
      }
      return { ...normalized, ...epochFields };
    }

    throw new Error("unsupported runtime operation: " + op);
  };
}
