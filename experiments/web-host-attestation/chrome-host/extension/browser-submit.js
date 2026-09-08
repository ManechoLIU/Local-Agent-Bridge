import { conversationIdFromChatgptUrl, selectExactTarget } from "./observation.js";
import { pageSubmitOperationFunction } from "./submit-page.js";
import { SUBMIT_CONFIRM_TIMEOUT_MS } from "./timeout-budget.js";

function requiredText(value, name, max) {
  const limit = max == null ? 8192 : max;
  const text = String(value == null ? "" : value).trim();
  if (!text) throw new Error(name + " is required");
  if (text.length > limit) throw new Error(name + " exceeds limit");
  return text;
}

function requiredGeneration(value, name) {
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < 1) {
    throw new Error(name + " must be a positive integer");
  }
  return number;
}

async function sha256Hex(value) {
  const data = new TextEncoder().encode(String(value));
  const digest = await globalThis.crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest))
    .map(byte => byte.toString(16).padStart(2, "0"))
    .join("");
}

function newDiagnostics({ wakeId, targetId }) {
  return {
    schema_version: 1,
    wake_id: wakeId,
    browser_target_id: targetId,
    phase: "start",
    debugger_attach: { attempted: false, ok: false },
    composer_preparation: {
      attempted: false,
      ok: false,
      status: null,
      rollback_attempted: false,
      rollback_succeeded: false
    },
    send_control_resolution: {
      attempted: false,
      ok: false,
      selector_kind: null,
      coordinates_changed_since_prepare: false
    },
    trusted_dispatch: {
      attempted: false,
      focus_emulation: false,
      mouse_moved: false,
      mouse_pressed: false,
      mouse_released: false
    },
    confirmation: {
      attempted: false,
      ok: false,
      composer_empty: null,
      exact_new_user_message: null
    },
    foreground: {
      mode: "background_focus_emulation",
      activated_target: false,
      restored_previous_active: false,
      user_changed_focus: false
    },
    failure_stage: null,
    dispatch_attempted: false,
    submit_confirmed: false,
    retryable: false
  };
}

function finalizeDiagnostics(trace, updates) {
  Object.assign(trace, updates || {});
  trace.dispatch_attempted = Boolean(trace.dispatch_attempted);
  trace.submit_confirmed = Boolean(trace.submit_confirmed);
  trace.retryable = Boolean(trace.retryable);
  return trace;
}

function browserReceipt({
  status,
  retryable,
  dispatchAttempted,
  submitConfirmed,
  receiptFactory,
  diagnostics
}) {
  return {
    dispatch_attempted: Boolean(dispatchAttempted),
    submit_confirmed: Boolean(submitConfirmed),
    retryable: Boolean(retryable),
    status,
    host_submit_receipt: dispatchAttempted
      ? "chrome_submit_" + receiptFactory()
      : null,
    submit_diagnostics: finalizeDiagnostics(diagnostics, {
      phase: submitConfirmed
        ? "confirmed"
        : dispatchAttempted
          ? "result_unknown"
          : "pre_dispatch_failure",
      dispatch_attempted: Boolean(dispatchAttempted),
      submit_confirmed: Boolean(submitConfirmed),
      retryable: Boolean(retryable)
    })
  };
}

async function scriptOperation(chromeApi, tabId, operation, input) {
  const results = await chromeApi.scripting.executeScript({
    target: { tabId },
    world: "ISOLATED",
    func: pageSubmitOperationFunction,
    args: [operation, input]
  });
  if (!Array.isArray(results) || results.length !== 1) {
    throw new Error(operation + " page result is unavailable");
  }
  return results[0] && results[0].result;
}

async function safeRollback(options) {
  const rollbackPreparedByHash = options.rollbackPreparedByHash;
  const diagnostics = options.diagnostics;
  diagnostics.composer_preparation.rollback_attempted = true;
  try {
    const result = await rollbackPreparedByHash({
      browser_target_id: options.browserTargetId,
      conversation_id: options.conversationId,
      payload_sha256: options.payloadSha256
    });
    const succeeded = Boolean(result && (result.cleared === true || result.already_absent === true));
    diagnostics.composer_preparation.rollback_succeeded = succeeded;
    return {
      succeeded,
      already_absent: Boolean(result && result.already_absent === true),
      status: result && result.status || (succeeded ? "rolled_back" : "rollback_unconfirmed")
    };
  } catch (error) {
    diagnostics.composer_preparation.rollback_succeeded = false;
    return {
      succeeded: false,
      already_absent: false,
      status: "rollback_error",
      error: String(error && error.message || error || "rollback failed")
    };
  }
}

export async function submitContinuationThroughChrome(options) {
  const chromeApi = options && options.chromeApi;
  const getTargets = options && options.getTargets;
  const observeTarget = options && options.observeTarget;
  const dispatchTrustedClick = options && options.dispatchTrustedClick;
  const rollbackPreparedByHash = options && options.rollbackPreparedByHash;
  const journal = options && options.journal;
  const message = options && options.message || {};
  const receiptFactory = options && options.receiptFactory || function () {
    return globalThis.crypto.randomUUID();
  };

  if (!chromeApi || typeof chromeApi.scripting?.executeScript !== "function") {
    throw new Error("chrome.scripting.executeScript is required");
  }
  if (typeof getTargets !== "function" || typeof observeTarget !== "function") {
    throw new Error("browser machine observation methods are required");
  }
  if (typeof dispatchTrustedClick !== "function") {
    throw new Error("trusted browser input dispatcher is required");
  }
  if (typeof rollbackPreparedByHash !== "function") {
    throw new Error("trusted prepared-payload rollback is required");
  }
  if (!journal || typeof journal.get !== "function" ||
      typeof journal.put !== "function" || typeof journal.remove !== "function") {
    throw new Error("submit crash journal is required");
  }

  const browserTargetId = requiredText(message.browser_target_id, "browser_target_id", 256);
  const conversationId = requiredText(message.conversation_id, "conversation_id", 128);
  const payload = requiredText(message.payload, "payload", 8000);
  const wakeId = requiredText(message.wake_id, "wake_id", 256);
  const targetGeneration = requiredGeneration(message.target_generation, "target_generation");
  const ownershipGeneration = requiredGeneration(message.ownership_generation, "ownership_generation");
  const payloadSha256 = await sha256Hex(payload);
  const trace = newDiagnostics({ wakeId, targetId: browserTargetId });

  const priorWake = await journal.get(wakeId);
  if (priorWake) {
    trace.failure_stage = "wake_replay";
    const replayConfirmed = priorWake.phase === "submit_confirmed";
    return browserReceipt({
      status: replayConfirmed
        ? "wake_replayed_submit_confirmed"
        : "wake_replayed_browser_journal",
      retryable: false,
      dispatchAttempted: Boolean(priorWake.dispatch_attempted),
      submitConfirmed: replayConfirmed,
      receiptFactory,
      diagnostics: trace
    });
  }

  let target;
  try {
    target = selectExactTarget(await getTargets(), browserTargetId);
    if (conversationIdFromChatgptUrl(target.url) !== conversationId) {
      throw new Error("wrong conversation before preparation");
    }
    const observation = await observeTarget(browserTargetId);
    if (!observation ||
        observation.browser_target_id !== browserTargetId ||
        observation.conversation_id !== conversationId) {
      throw new Error("machine target changed before preparation");
    }
  } catch (error) {
    trace.failure_stage = "target_preflight";
    return browserReceipt({
      status: "machine_target_unavailable_before_dispatch",
      retryable: false,
      dispatchAttempted: false,
      submitConfirmed: false,
      receiptFactory,
      diagnostics: trace
    });
  }
  if (!Number.isInteger(target.tabId)) {
    trace.failure_stage = "target_preflight";
    return browserReceipt({
      status: "target_tab_unavailable_before_dispatch",
      retryable: false,
      dispatchAttempted: false,
      submitConfirmed: false,
      receiptFactory,
      diagnostics: trace
    });
  }

  await journal.put({
    wake_id: wakeId,
    browser_target_id: browserTargetId,
    conversation_id: conversationId,
    target_generation: targetGeneration,
    ownership_generation: ownershipGeneration,
    payload_sha256: payloadSha256,
    phase: "preparing",
    dispatch_attempted: false,
    updated_at_ms: Date.now()
  });

  trace.composer_preparation.attempted = true;
  let prepared;
  try {
    prepared = await scriptOperation(chromeApi, target.tabId, "prepare", {
      payload,
      expected_conversation_id: conversationId
    });
  } catch (error) {
    trace.composer_preparation.status = "prepare_result_unavailable";
    trace.failure_stage = "composer_prepare";
    const rollback = await safeRollback({
      rollbackPreparedByHash,
      diagnostics: trace,
      browserTargetId,
      conversationId,
      payloadSha256
    });
    if (rollback.succeeded) await journal.remove(wakeId);
    else await journal.put({
      wake_id: wakeId,
      browser_target_id: browserTargetId,
      conversation_id: conversationId,
      target_generation: targetGeneration,
      ownership_generation: ownershipGeneration,
      payload_sha256: payloadSha256,
      phase: "rollback_unconfirmed",
      dispatch_attempted: false,
      updated_at_ms: Date.now()
    });
    return browserReceipt({
      status: rollback.succeeded ? "prepare_result_unavailable" : "rollback_unconfirmed",
      retryable: rollback.succeeded,
      dispatchAttempted: false,
      submitConfirmed: false,
      receiptFactory,
      diagnostics: trace
    });
  }

  if (!prepared || typeof prepared.prepared !== "boolean") {
    trace.composer_preparation.status = "prepare_result_invalid";
    trace.failure_stage = "composer_prepare";
    const rollback = await safeRollback({
      rollbackPreparedByHash,
      diagnostics: trace,
      browserTargetId,
      conversationId,
      payloadSha256
    });
    if (rollback.succeeded) {
      await journal.remove(wakeId);
    } else {
      await journal.put({
        wake_id: wakeId,
        browser_target_id: browserTargetId,
        conversation_id: conversationId,
        target_generation: targetGeneration,
        ownership_generation: ownershipGeneration,
        payload_sha256: payloadSha256,
        phase: "rollback_unconfirmed",
        dispatch_attempted: false,
        updated_at_ms: Date.now()
      });
    }
    return browserReceipt({
      status: rollback.succeeded ? "prepare_result_invalid" : "rollback_unconfirmed",
      retryable: rollback.succeeded,
      dispatchAttempted: false,
      submitConfirmed: false,
      receiptFactory,
      diagnostics: trace
    });
  }

  trace.composer_preparation.ok = prepared.prepared === true;
  const rawPrepareStatus = String(prepared.status == null ? "" : prepared.status).trim();
  const prepareStatusValid = rawPrepareStatus.length > 0 && rawPrepareStatus.length <= 128;
  const cleanPrepareFailure = prepareStatusValid && new Set([
    "invalid_payload",
    "wrong_conversation",
    "composer_unavailable",
    "composer_not_empty",
    "controller_active"
  ]).has(rawPrepareStatus);
  trace.composer_preparation.status = prepareStatusValid
    ? rawPrepareStatus
    : "prepare_result_invalid";

  if (!prepared.prepared) {
    trace.failure_stage = "composer_preflight";
    if (cleanPrepareFailure) {
      await journal.remove(wakeId);
      return browserReceipt({
        status: rawPrepareStatus,
        retryable: Boolean(prepared.retryable),
        dispatchAttempted: false,
        submitConfirmed: false,
        receiptFactory,
        diagnostics: trace
      });
    }

    const rollback = await safeRollback({
      rollbackPreparedByHash,
      diagnostics: trace,
      browserTargetId,
      conversationId,
      payloadSha256
    });
    if (rollback.succeeded) {
      await journal.remove(wakeId);
    } else {
      await journal.put({
        wake_id: wakeId,
        browser_target_id: browserTargetId,
        conversation_id: conversationId,
        target_generation: targetGeneration,
        ownership_generation: ownershipGeneration,
        payload_sha256: payloadSha256,
        phase: "rollback_unconfirmed",
        dispatch_attempted: false,
        updated_at_ms: Date.now()
      });
    }
    return browserReceipt({
      status: rollback.succeeded
        ? (prepareStatusValid ? rawPrepareStatus : "prepare_result_invalid")
        : "rollback_unconfirmed",
      retryable: false,
      dispatchAttempted: false,
      submitConfirmed: false,
      receiptFactory,
      diagnostics: trace
    });
  }

  await journal.put({
    wake_id: wakeId,
    browser_target_id: browserTargetId,
    conversation_id: conversationId,
    target_generation: targetGeneration,
    ownership_generation: ownershipGeneration,
    payload_sha256: payloadSha256,
    phase: "prepared",
    dispatch_attempted: false,
    updated_at_ms: Date.now()
  });

  try {
    const beforeDispatch = await observeTarget(browserTargetId);
    if (!beforeDispatch ||
        beforeDispatch.browser_target_id !== browserTargetId ||
        beforeDispatch.conversation_id !== conversationId) {
      throw new Error("machine target changed before trusted dispatch");
    }
  } catch (error) {
    trace.failure_stage = "target_before_dispatch";
    const rollback = await safeRollback({
      rollbackPreparedByHash,
      diagnostics: trace,
      browserTargetId,
      conversationId,
      payloadSha256
    });
    if (rollback.succeeded) {
      await journal.remove(wakeId);
    } else {
      await journal.put({
        wake_id: wakeId,
        browser_target_id: browserTargetId,
        conversation_id: conversationId,
        target_generation: targetGeneration,
        ownership_generation: ownershipGeneration,
        payload_sha256: payloadSha256,
        phase: "rollback_unconfirmed",
        dispatch_attempted: false,
        updated_at_ms: Date.now()
      });
    }
    return browserReceipt({
      status: rollback.succeeded
        ? "machine_target_changed_before_trusted_dispatch"
        : "rollback_unconfirmed",
      retryable: false,
      dispatchAttempted: false,
      submitConfirmed: false,
      receiptFactory,
      diagnostics: trace
    });
  }

  let dispatch;
  try {
    dispatch = await dispatchTrustedClick({
      browser_target_id: browserTargetId,
      conversation_id: conversationId,
      payload,
      initial_send_control: prepared.initial_send_control || null,
      on_before_dispatch: async function (dispatchDiagnostics) {
        trace.debugger_attach = {
          attempted: true,
          ok: Boolean(dispatchDiagnostics && dispatchDiagnostics.debugger_attached)
        };
        trace.send_control_resolution = {
          attempted: true,
          ok: Boolean(dispatchDiagnostics && dispatchDiagnostics.send_control_resolved),
          selector_kind: dispatchDiagnostics && dispatchDiagnostics.selector_kind || null,
          coordinates_changed_since_prepare: Boolean(
            dispatchDiagnostics && dispatchDiagnostics.coordinates_changed_since_prepare
          )
        };
        trace.trusted_dispatch.focus_emulation = Boolean(
          dispatchDiagnostics && dispatchDiagnostics.focus_emulation
        );
        trace.trusted_dispatch.attempted = false;
        trace.dispatch_attempted = false;
        await journal.put({
          wake_id: wakeId,
          browser_target_id: browserTargetId,
          conversation_id: conversationId,
          target_generation: targetGeneration,
          ownership_generation: ownershipGeneration,
          payload_sha256: payloadSha256,
          phase: "dispatch_armed",
          dispatch_attempted: false,
          updated_at_ms: Date.now()
        });
      }
    });
    if (dispatch && dispatch.diagnostics) {
      trace.debugger_attach = {
        attempted: true,
        ok: Boolean(dispatch.diagnostics.debugger_attached)
      };
      trace.send_control_resolution = {
        attempted: true,
        ok: Boolean(dispatch.diagnostics.send_control_resolved),
        selector_kind: dispatch.diagnostics.selector_kind || null,
        coordinates_changed_since_prepare: Boolean(
          dispatch.diagnostics.coordinates_changed_since_prepare
        )
      };
      trace.trusted_dispatch = {
        attempted: Boolean(dispatch.dispatch_attempted),
        focus_emulation: Boolean(dispatch.diagnostics.focus_emulation),
        mouse_moved: Boolean(dispatch.diagnostics.mouse_moved),
        mouse_pressed: Boolean(dispatch.diagnostics.mouse_pressed),
        mouse_released: Boolean(dispatch.diagnostics.mouse_released)
      };
    }
  } catch (error) {
    const dispatchAttempted = error && error.dispatch_attempted === true;
    trace.failure_stage = String(error && error.failure_stage || "trusted_dispatch");
    if (error && error.diagnostics) {
      trace.debugger_attach = {
        attempted: Boolean(error.diagnostics.debugger_attach_attempted),
        ok: Boolean(error.diagnostics.debugger_attached)
      };
      trace.send_control_resolution = {
        attempted: Boolean(error.diagnostics.send_control_resolution_attempted),
        ok: Boolean(error.diagnostics.send_control_resolved),
        selector_kind: error.diagnostics.selector_kind || null,
        coordinates_changed_since_prepare: Boolean(
          error.diagnostics.coordinates_changed_since_prepare
        )
      };
      trace.trusted_dispatch = {
        attempted: dispatchAttempted,
        focus_emulation: Boolean(error.diagnostics.focus_emulation),
        mouse_moved: Boolean(error.diagnostics.mouse_moved),
        mouse_pressed: Boolean(error.diagnostics.mouse_pressed),
        mouse_released: Boolean(error.diagnostics.mouse_released)
      };
    }
    if (dispatchAttempted) {
      trace.dispatch_attempted = true;
      await journal.put({
        wake_id: wakeId,
        browser_target_id: browserTargetId,
        conversation_id: conversationId,
        target_generation: targetGeneration,
        ownership_generation: ownershipGeneration,
        payload_sha256: payloadSha256,
        phase: "result_unknown",
        dispatch_attempted: true,
        updated_at_ms: Date.now()
      });
      return browserReceipt({
        status: "result_unknown",
        retryable: false,
        dispatchAttempted: true,
        submitConfirmed: false,
        receiptFactory,
        diagnostics: trace
      });
    }

    const rollback = await safeRollback({
      rollbackPreparedByHash,
      diagnostics: trace,
      browserTargetId,
      conversationId,
      payloadSha256
    });
    if (rollback.succeeded) await journal.remove(wakeId);
    else await journal.put({
      wake_id: wakeId,
      browser_target_id: browserTargetId,
      conversation_id: conversationId,
      target_generation: targetGeneration,
      ownership_generation: ownershipGeneration,
      payload_sha256: payloadSha256,
      phase: "rollback_unconfirmed",
      dispatch_attempted: false,
      updated_at_ms: Date.now()
    });
    return browserReceipt({
      status: rollback.succeeded
        ? String(error && error.status || "trusted_dispatch_failed_before_dispatch")
        : "rollback_unconfirmed",
      retryable: rollback.succeeded && Boolean(error && error.retryable),
      dispatchAttempted: false,
      submitConfirmed: false,
      receiptFactory,
      diagnostics: trace
    });
  }

  trace.dispatch_attempted = Boolean(dispatch && dispatch.dispatch_attempted);
  trace.confirmation.attempted = true;
  let confirmation;
  try {
    confirmation = await scriptOperation(chromeApi, target.tabId, "confirm", {
      payload,
      expected_conversation_id: conversationId,
      baseline_user_message_count: prepared.baseline_user_message_count,
      timeout_ms: SUBMIT_CONFIRM_TIMEOUT_MS
    });
  } catch (error) {
    trace.failure_stage = "confirmation";
    await journal.put({
      wake_id: wakeId,
      browser_target_id: browserTargetId,
      conversation_id: conversationId,
      target_generation: targetGeneration,
      ownership_generation: ownershipGeneration,
      payload_sha256: payloadSha256,
      phase: "result_unknown",
      dispatch_attempted: true,
      updated_at_ms: Date.now()
    });
    return browserReceipt({
      status: "result_unknown",
      retryable: false,
      dispatchAttempted: true,
      submitConfirmed: false,
      receiptFactory,
      diagnostics: trace
    });
  }

  const confirmed = Boolean(
    confirmation &&
    confirmation.dispatch_attempted === true &&
    confirmation.submit_confirmed === true &&
    confirmation.exact_new_user_message === true
  );
  trace.confirmation.ok = confirmed;
  trace.confirmation.composer_empty = confirmation && typeof confirmation.composer_empty === "boolean"
    ? confirmation.composer_empty
    : null;
  trace.confirmation.exact_new_user_message = Boolean(
    confirmation && confirmation.exact_new_user_message
  );

  if (confirmed) {
    trace.submit_confirmed = true;
    await journal.put({
      wake_id: wakeId,
      browser_target_id: browserTargetId,
      conversation_id: conversationId,
      target_generation: targetGeneration,
      ownership_generation: ownershipGeneration,
      payload_sha256: payloadSha256,
      phase: "submit_confirmed",
      dispatch_attempted: true,
      updated_at_ms: Date.now()
    });
    return browserReceipt({
      status: "submit_confirmed",
      retryable: false,
      dispatchAttempted: true,
      submitConfirmed: true,
      receiptFactory,
      diagnostics: trace
    });
  }

  trace.failure_stage = "confirmation";
  await journal.put({
    wake_id: wakeId,
    browser_target_id: browserTargetId,
    conversation_id: conversationId,
    target_generation: targetGeneration,
    ownership_generation: ownershipGeneration,
    payload_sha256: payloadSha256,
    phase: "result_unknown",
    dispatch_attempted: true,
    updated_at_ms: Date.now()
  });
  return browserReceipt({
    status: "result_unknown",
    retryable: false,
    dispatchAttempted: true,
    submitConfirmed: false,
    receiptFactory,
    diagnostics: trace
  });
}
