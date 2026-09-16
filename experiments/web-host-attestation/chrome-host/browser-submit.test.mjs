import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { reconcileContinuationThroughChrome, submitContinuationThroughChrome } from "./extension/browser-submit.js";

const CONVERSATION = "6a9e4158-cc8c-83ea-9763-05a7fd51f597";
const OTHER = "6a9e41a2-266c-83ea-9104-e768ed4ad3f2";

function target(conversation = CONVERSATION) {
  return {
    id: "target-1",
    tabId: 9,
    type: "page",
    url: "https://chatgpt.com/g/g-p-local/c/" + conversation
  };
}

function memoryJournal(seed = []) {
  const records = new Map(seed.map(record => [record.wake_id, { ...record }]));
  return {
    records,
    async get(wakeId) { return records.get(wakeId) || null; },
    async put(record) { records.set(record.wake_id, { ...record }); return record; },
    async remove(wakeId) { records.delete(wakeId); },
    async list() { return [...records.values()]; }
  };
}

function fixture(overrides = {}) {
  const calls = [];
  let scriptIndex = 0;
  const journal = overrides.journal || memoryJournal();
  const prepare = overrides.prepareResult || {
    prepared: true,
    ready_to_dispatch: true,
    dispatch_attempted: false,
    submit_confirmed: false,
    retryable: false,
    status: "prepared",
    baseline_user_message_count: 2,
    initial_send_control: {
      resolved: true,
      selector_kind: "testid_send_button",
      x: 100,
      y: 200
    }
  };
  const confirm = overrides.confirmResult || {
    dispatch_attempted: true,
    submit_confirmed: true,
    retryable: false,
    status: "submit_confirmed",
    composer_empty: true,
    exact_new_user_message: true
  };

  let observeCalls = 0;
  const options = {
    chromeApi: {
      scripting: {
        async executeScript(input) {
          scriptIndex += 1;
          calls.push(["script", input.args[0]]);
          if (overrides.executeThrowsAt === scriptIndex) throw new Error("script result lost");
          return [{
            frameId: 0,
            result: input.args[0] === "prepare" ? prepare : confirm
          }];
        }
      }
    },
    getTargets: async () => [target(overrides.targetConversation || CONVERSATION)],
    observeTarget: async () => {
      observeCalls += 1;
      return {
        browser_target_id: "target-1",
        conversation_id: observeCalls > 1 && overrides.observedConversationAfterFirst
          ? overrides.observedConversationAfterFirst
          : overrides.observedConversation || CONVERSATION
      };
    },
    dispatchTrustedClick: async input => {
      calls.push(["trusted_click", input.browser_target_id]);
      if (overrides.trustedClickError) throw overrides.trustedClickError;
      if (typeof input.on_before_dispatch === "function") {
        await input.on_before_dispatch({
          debugger_attached: true,
          send_control_resolved: true,
          selector_kind: "testid_send_button",
          coordinates_changed_since_prepare: Boolean(overrides.coordinatesChanged),
          focus_emulation: true
        });
      }
      if (overrides.trustedClickErrorAfterArm) {
        throw overrides.trustedClickErrorAfterArm;
      }
      return {
        dispatched: true,
        dispatch_attempted: true,
        diagnostics: {
          debugger_attached: true,
          send_control_resolved: true,
          selector_kind: "testid_send_button",
          coordinates_changed_since_prepare: Boolean(overrides.coordinatesChanged),
          focus_emulation: true,
          mouse_moved: true,
          mouse_pressed: true,
          mouse_released: true
        }
      };
    },
    rollbackPreparedByHash: async record => {
      calls.push(["rollback", record.payload_sha256]);
      if (overrides.rollbackThrows) throw new Error("rollback failed");
      return overrides.rollbackResult || {
        cleared: true,
        already_absent: false,
        status: "rolled_back"
      };
    },
    journal,
    message: {
      browser_target_id: "target-1",
      conversation_id: CONVERSATION,
      payload: "resume",
      wake_id: "wake-1",
      wake_nonce: "nonce-1",
      target_generation: 4,
      ownership_generation: 8
    },
    receiptFactory: () => "receipt-1"
  };
  return { options, calls, journal };
}

function preDispatchError({
  status = "debugger_attach_failed",
  retryable = true,
  failureStage = "debugger_attach"
} = {}) {
  const error = new Error(status);
  error.dispatch_attempted = false;
  error.retryable = retryable;
  error.status = status;
  error.failure_stage = failureStage;
  error.diagnostics = {
    debugger_attach_attempted: true,
    debugger_attached: false,
    send_control_resolution_attempted: false,
    send_control_resolved: false,
    focus_emulation: false,
    mouse_moved: false,
    mouse_pressed: false,
    mouse_released: false
  };
  return error;
}

function uncertainDispatchError() {
  const error = new Error("mouse press uncertain");
  error.dispatch_attempted = true;
  error.retryable = false;
  error.status = "trusted_dispatch_result_unknown";
  error.failure_stage = "mouse_press";
  error.diagnostics = {
    debugger_attach_attempted: true,
    debugger_attached: true,
    send_control_resolution_attempted: true,
    send_control_resolved: true,
    selector_kind: "testid_send_button",
    focus_emulation: true,
    mouse_moved: true,
    mouse_pressed: false,
    mouse_released: false
  };
  return error;
}

function reconcileFixture(overrides = {}) {
  const payloadSha256 = overrides.payloadSha256 || createHash("sha256").update("resume").digest("hex");
  const pageResults = overrides.pageResults || [{
    status: "observed",
    conversation_matches: true,
    composer_exact_payload: true,
    composer_empty: false,
    exact_user_message_present: false
  }];
  const observations = overrides.observations || [{
    browser_target_id: "target-1",
    conversation_id: CONVERSATION
  }];
  const journal = overrides.journal || memoryJournal([{
    schema_version: 1,
    wake_id: "wake-reconcile-hash",
    browser_target_id: "target-1",
    conversation_id: CONVERSATION,
    target_generation: 4,
    ownership_generation: 8,
    payload_sha256: payloadSha256,
    phase: "result_unknown",
    dispatch_attempted: true,
    baseline_user_message_count: 1,
    updated_at_ms: 1
  }]);
  const scriptInputs = [];
  const observeCalls = [];
  return {
    payloadSha256,
    journal,
    scriptInputs,
    observeCalls,
    options: {
      chromeApi: {
        scripting: {
          async executeScript(input) {
            scriptInputs.push(input.args[1]);
            if (overrides.scriptError) throw overrides.scriptError;
            const result = pageResults[Math.min(scriptInputs.length - 1, pageResults.length - 1)];
            return [{ frameId: 0, result }];
          }
        }
      },
      getTargets: async () => [target(overrides.targetConversation)],
      observeTarget: async (targetId) => {
        observeCalls.push(targetId);
        if (overrides.observeError && observeCalls.length > (overrides.observeErrorAfter || 0)) {
          throw overrides.observeError;
        }
        return observations[Math.min(observeCalls.length - 1, observations.length - 1)];
      },
      journal,
      sleep: overrides.sleep || (async () => {}),
      message: {
        browser_target_id: "target-1",
        conversation_id: CONVERSATION,
        payload_sha256: payloadSha256,
        wake_id: "wake-reconcile-hash",
        target_generation: 4,
        ownership_generation: 8
      }
    }
  };
}

test("reconcile continuation uses durable RESULT_UNKNOWN binding and payload hash without prompt text", async () => {
  const f = reconcileFixture();
  const result = await reconcileContinuationThroughChrome(f.options);
  assert.equal(result.journal_binding_matched, true);
  assert.equal(result.journal_phase, "result_unknown");
  assert.equal(result.composer_exact_payload, true);
  assert.equal(result.baseline_user_message_count, 1);
  assert.equal(f.scriptInputs[0].expected_payload_sha256, f.payloadSha256);
  assert.equal(f.scriptInputs[0].baseline_user_message_count, 1);
  assert.equal("payload" in f.scriptInputs[0], false);
  assert.equal("wake_nonce" in f.scriptInputs[0], false);
});

test("missing journal baseline cannot be confirmed not delivered", async () => {
  const payloadSha256 = createHash("sha256").update("resume").digest("hex");
  const f = reconcileFixture({
    journal: memoryJournal([{
      schema_version: 1,
      wake_id: "wake-reconcile-hash",
      browser_target_id: "target-1",
      conversation_id: CONVERSATION,
      target_generation: 4,
      ownership_generation: 8,
      payload_sha256: payloadSha256,
      phase: "result_unknown",
      dispatch_attempted: true,
      updated_at_ms: 1
    }])
  });
  const result = await reconcileContinuationThroughChrome(f.options);
  assert.equal(result.status, "invalid_baseline_user_message_count");
  assert.equal(result.journal_binding_matched, true);
  assert.equal(result.baseline_user_message_count, null);
  assert.equal(result.exact_user_message_present, null);
  assert.equal(result.composer_exact_payload, null);
  assert.equal(f.scriptInputs.length, 0);
});

test("first stable page observation then exact new user message is not clearable", async () => {
  const f = reconcileFixture({
    pageResults: [
      {
        status: "observed",
        conversation_matches: true,
        composer_exact_payload: true,
        composer_empty: false,
        exact_user_message_present: false
      },
      {
        status: "observed",
        conversation_matches: true,
        composer_exact_payload: false,
        composer_empty: true,
        exact_user_message_present: true
      }
    ]
  });
  const result = await reconcileContinuationThroughChrome(f.options);
  assert.equal(result.exact_user_message_present, true);
  assert.equal(result.stable_not_delivered, false);
  assert.equal(f.scriptInputs.length, 2);
  assert.equal(f.observeCalls.length >= 2, true);
});

test("two stable composer observations with no new message are stable not delivered", async () => {
  const f = reconcileFixture();
  const result = await reconcileContinuationThroughChrome(f.options);
  assert.equal(result.exact_user_message_present, false);
  assert.equal(result.composer_exact_payload, true);
  assert.equal(result.composer_empty, false);
  assert.equal(result.stable_not_delivered, true);
  assert.equal(result.conversation_matches, true);
  assert.equal(result.target_matches, true);
  assert.equal(f.scriptInputs.length, 2);
});

test("second target or conversation drift stays unresolved", async () => {
  const f = reconcileFixture({
    observations: [
      { browser_target_id: "target-1", conversation_id: CONVERSATION },
      { browser_target_id: "target-1", conversation_id: OTHER }
    ]
  });
  const result = await reconcileContinuationThroughChrome(f.options);
  assert.equal(result.status, "observation_unresolved");
  assert.equal(result.stable_not_delivered, null);
  assert.notEqual(result.stable_not_delivered, false);
  assert.notEqual(result.status, "confirmed_not_delivered");
  assert.equal(result.conversation_matches, null);
  assert.equal(result.target_matches, null);
});

test("composer empty on either reconcile observation is not stable not delivered", async () => {
  const firstEmpty = await reconcileContinuationThroughChrome(reconcileFixture({
    pageResults: [{
      status: "observed",
      conversation_matches: true,
      composer_exact_payload: false,
      composer_empty: true,
      exact_user_message_present: false
    }]
  }).options);
  assert.equal(firstEmpty.composer_empty, true);
  assert.equal(firstEmpty.stable_not_delivered, false);

  const secondEmpty = await reconcileContinuationThroughChrome(reconcileFixture({
    pageResults: [
      {
        status: "observed",
        conversation_matches: true,
        composer_exact_payload: true,
        composer_empty: false,
        exact_user_message_present: false
      },
      {
        status: "observed",
        conversation_matches: true,
        composer_exact_payload: false,
        composer_empty: true,
        exact_user_message_present: false
      }
    ]
  }).options);
  assert.equal(secondEmpty.composer_empty, true);
  assert.equal(secondEmpty.stable_not_delivered, false);
  assert.equal(secondEmpty.exact_user_message_present, false);
});

test("reconcile transport failure keeps unknown evidence null", async () => {
  const f = reconcileFixture({
    scriptError: new Error("script transport lost")
  });
  const result = await reconcileContinuationThroughChrome(f.options);
  assert.equal(result.status, "observation_unresolved");
  assert.equal(result.conversation_matches, null);
  assert.equal(result.target_matches, null);
  assert.equal(result.composer_exact_payload, null);
  assert.equal(result.composer_empty, null);
  assert.equal(result.exact_user_message_present, null);
  assert.equal(result.stable_not_delivered, null);
  assert.notEqual(result.stable_not_delivered, false);
});

test("incomplete page evidence does not manufacture stable_not_delivered false", async () => {
  const f = reconcileFixture({
    pageResults: [{
      status: "observed",
      conversation_matches: true
    }]
  });
  const result = await reconcileContinuationThroughChrome(f.options);
  assert.equal(result.conversation_matches, true);
  assert.equal(result.composer_exact_payload, null);
  assert.equal(result.composer_empty, null);
  assert.equal(result.exact_user_message_present, null);
  assert.equal(result.stable_not_delivered, null);
  assert.notEqual(result.stable_not_delivered, false);
  assert.notEqual(result.status, "confirmed_not_delivered");
});

test("wrong target fails before journal preparation or browser side effects", async () => {
  const f = fixture({ targetConversation: OTHER });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.dispatch_attempted, false);
  assert.equal(result.status, "machine_target_unavailable_before_dispatch");
  assert.equal(f.journal.records.size, 0);
  assert.equal(f.calls.length, 0);
});

test("existing user composer content is never overwritten or deleted", async () => {
  const f = fixture({
    prepareResult: {
      prepared: false,
      ready_to_dispatch: false,
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: false,
      status: "composer_not_empty",
      baseline_user_message_count: 2,
      initial_send_control: null
    }
  });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.status, "composer_not_empty");
  assert.equal(result.dispatch_attempted, false);
  assert.equal(f.calls.some(call => call[0] === "rollback"), false);
  assert.equal(f.journal.records.size, 0);
});
test("trusted click pre-dispatch error rolls back exact Host payload and stays dispatch_attempted=false", async () => {
  const f = fixture({ trustedClickError: preDispatchError() });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.dispatch_attempted, false);
  assert.equal(result.submit_confirmed, false);
  assert.equal(result.retryable, true);
  assert.equal(result.status, "debugger_attach_failed");
  assert.equal(f.calls.filter(call => call[0] === "rollback").length, 1);
  assert.equal(f.journal.records.size, 0);
  assert.equal(result.submit_diagnostics.composer_preparation.rollback_succeeded, true);
});

test("pre-dispatch error with unprovable rollback becomes non-retryable and retains journal", async () => {
  const f = fixture({
    trustedClickError: preDispatchError(),
    rollbackResult: {
      cleared: false,
      already_absent: false,
      status: "composer_hash_mismatch"
    }
  });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.dispatch_attempted, false);
  assert.equal(result.retryable, false);
  assert.equal(result.status, "rollback_unconfirmed");
  assert.equal(f.journal.records.get("wake-1").phase, "rollback_unconfirmed");
});

test("invalid prepare result with unprovable rollback persists rollback_unconfirmed", async () => {
  const f = fixture({
    prepareResult: { status: "malformed" },
    rollbackResult: {
      cleared: false,
      already_absent: false,
      status: "composer_hash_mismatch"
    }
  });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.status, "rollback_unconfirmed");
  assert.equal(result.retryable, false);
  assert.equal(result.dispatch_attempted, false);
  assert.equal(f.journal.records.get("wake-1").phase, "rollback_unconfirmed");
});

test("prepared false with missing status requires exact rollback and is never retryable", async () => {
  const f = fixture({
    prepareResult: {
      prepared: false,
      ready_to_dispatch: false,
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: true,
      baseline_user_message_count: 2,
      initial_send_control: null
    }
  });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.status, "prepare_result_invalid");
  assert.equal(result.retryable, false);
  assert.equal(result.dispatch_attempted, false);
  assert.equal(f.calls.filter(call => call[0] === "rollback").length, 1);
  assert.equal(f.journal.records.size, 0);
});

test("unknown prepared-false status requires exact rollback instead of trusting retryable", async () => {
  const f = fixture({
    prepareResult: {
      prepared: false,
      ready_to_dispatch: false,
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: true,
      status: "future_prepare_state",
      baseline_user_message_count: 2,
      initial_send_control: null
    }
  });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.status, "future_prepare_state");
  assert.equal(result.retryable, false);
  assert.equal(result.dispatch_attempted, false);
  assert.equal(f.calls.filter(call => call[0] === "rollback").length, 1);
  assert.equal(f.journal.records.size, 0);
});

test("partial composer preparation performs exact rollback before returning", async () => {
  const f = fixture({
    prepareResult: {
      prepared: false,
      ready_to_dispatch: false,
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: false,
      status: "composer_preparation_unconfirmed",
      baseline_user_message_count: 2,
      initial_send_control: null
    }
  });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.status, "composer_preparation_unconfirmed");
  assert.equal(result.dispatch_attempted, false);
  assert.equal(result.retryable, false);
  assert.equal(f.calls.filter(call => call[0] === "rollback").length, 1);
  assert.equal(f.journal.records.size, 0);
});

test("partial composer preparation with unprovable ownership retains journal and never retries", async () => {
  const f = fixture({
    prepareResult: {
      prepared: false,
      ready_to_dispatch: false,
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: false,
      status: "composer_preparation_unconfirmed",
      baseline_user_message_count: 2,
      initial_send_control: null
    },
    rollbackResult: {
      cleared: false,
      already_absent: false,
      status: "composer_hash_mismatch"
    }
  });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.status, "rollback_unconfirmed");
  assert.equal(result.dispatch_attempted, false);
  assert.equal(result.retryable, false);
  assert.equal(f.journal.records.get("wake-1").phase, "rollback_unconfirmed");
});

test("prepare_result_invalid with unprovable rollback records rollback_unconfirmed", async () => {
  const f = fixture({
    prepareResult: {},
    rollbackResult: {
      cleared: false,
      already_absent: false,
      status: "composer_hash_mismatch"
    }
  });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.status, "rollback_unconfirmed");
  assert.equal(result.dispatch_attempted, false);
  assert.equal(f.journal.records.get("wake-1").phase, "rollback_unconfirmed");
});

test("target drift before trusted dispatch with rollback failure records rollback_unconfirmed", async () => {
  const f = fixture({
    observedConversationAfterFirst: OTHER,
    rollbackResult: {
      cleared: false,
      already_absent: false,
      status: "composer_hash_mismatch"
    }
  });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.status, "rollback_unconfirmed");
  assert.equal(result.dispatch_attempted, false);
  assert.equal(f.journal.records.get("wake-1").phase, "rollback_unconfirmed");
});

test("live failure after dispatch_armed but before mousePressed exact-rolls back and remains pre-dispatch", async () => {
  const error = preDispatchError({
    status: "target_frame_changed_after_dispatch_arm",
    retryable: false,
    failureStage: "dispatch_arm_target_validation"
  });
  const f = fixture({ trustedClickErrorAfterArm: error });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.dispatch_attempted, false);
  assert.equal(result.status, "target_frame_changed_after_dispatch_arm");
  assert.equal(f.calls.filter(call => call[0] === "rollback").length, 1);
  assert.equal(f.journal.records.size, 0);
});

test("possible dispatch with unknown result never rolls back and remains RESULT_UNKNOWN", async () => {
  const f = fixture({ trustedClickError: uncertainDispatchError() });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.dispatch_attempted, true);
  assert.equal(result.submit_confirmed, false);
  assert.equal(result.retryable, false);
  assert.equal(result.status, "result_unknown");
  assert.equal(f.calls.some(call => call[0] === "rollback"), false);
  assert.equal(f.journal.records.get("wake-1").dispatch_attempted, true);
  assert.equal(f.journal.records.get("wake-1").phase, "result_unknown");
  assert.equal(f.journal.records.get("wake-1").baseline_user_message_count, 2);
});

test("lost confirmation after trusted dispatch is result unknown and never rolls back", async () => {
  const f = fixture({ executeThrowsAt: 2 });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.dispatch_attempted, true);
  assert.equal(result.submit_confirmed, false);
  assert.equal(result.retryable, false);
  assert.equal(result.status, "result_unknown");
  assert.equal(f.calls.some(call => call[0] === "rollback"), false);
});

test("dispatch_armed journal record keeps prepare baseline", async () => {
  const f = fixture();
  const phases = [];
  const originalPut = f.journal.put.bind(f.journal);
  f.journal.put = async record => {
    phases.push({
      phase: record.phase,
      baseline: record.baseline_user_message_count
    });
    return originalPut(record);
  };
  await submitContinuationThroughChrome(f.options);
  const armed = phases.find(entry => entry.phase === "dispatch_armed");
  assert.equal(armed.baseline, 2);
});

test("submit confirmed requires exact new user-message evidence and retains a replay tombstone", async () => {
  const f = fixture();
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.dispatch_attempted, true);
  assert.equal(result.submit_confirmed, true);
  assert.equal(result.status, "submit_confirmed");
  assert.equal(result.submit_diagnostics.confirmation.exact_new_user_message, true);
  assert.equal(result.submit_diagnostics.confirmation.composer_empty, true);
  assert.equal(result.submit_diagnostics.foreground.mode, "background_focus_emulation");
  assert.equal(result.submit_diagnostics.foreground.activated_target, false);
  assert.equal(f.journal.records.size, 1);
  assert.equal(f.journal.records.get("wake-1").phase, "submit_confirmed");
  assert.equal(f.journal.records.get("wake-1").dispatch_attempted, true);
  assert.equal(f.journal.records.get("wake-1").baseline_user_message_count, 2);
});

test("structured Host diagnostics records stages without prompt body", async () => {
  const f = fixture({ coordinatesChanged: true });
  const result = await submitContinuationThroughChrome(f.options);
  const trace = result.submit_diagnostics;
  assert.equal(trace.wake_id, "wake-1");
  assert.equal(trace.browser_target_id, "target-1");
  assert.equal(trace.debugger_attach.ok, true);
  assert.equal(trace.composer_preparation.ok, true);
  assert.equal(trace.send_control_resolution.coordinates_changed_since_prepare, true);
  assert.equal(trace.trusted_dispatch.mouse_pressed, true);
  assert.equal(trace.confirmation.ok, true);
  assert.equal(JSON.stringify(trace).includes("resume"), false);
});

test("exact same browser wake journal replay is rejected without a second dispatch", async () => {
  const journal = memoryJournal([{
    schema_version: 1,
    wake_id: "wake-1",
    browser_target_id: "target-1",
    conversation_id: CONVERSATION,
    target_generation: 4,
    ownership_generation: 8,
    payload_sha256: "a".repeat(64),
    phase: "result_unknown",
    dispatch_attempted: true,
    updated_at_ms: 1
  }]);
  const f = fixture({ journal });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.status, "wake_replayed_browser_journal");
  assert.equal(result.dispatch_attempted, true);
  assert.equal(f.calls.length, 0);
});

test("confirmed wake tombstone replay preserves confirmation without a second dispatch", async () => {
  const journal = memoryJournal([{
    schema_version: 1,
    wake_id: "wake-1",
    browser_target_id: "target-1",
    conversation_id: CONVERSATION,
    target_generation: 4,
    ownership_generation: 8,
    payload_sha256: "a".repeat(64),
    phase: "submit_confirmed",
    dispatch_attempted: true,
    updated_at_ms: 1
  }]);
  const f = fixture({ journal });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.status, "wake_replayed_submit_confirmed");
  assert.equal(result.dispatch_attempted, true);
  assert.equal(result.submit_confirmed, true);
  assert.equal(result.retryable, false);
  assert.equal(f.calls.length, 0);
});

test("controller-active preflight never writes payload and remains safely retryable", async () => {
  const f = fixture({
    prepareResult: {
      prepared: false,
      ready_to_dispatch: false,
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: true,
      status: "controller_active",
      baseline_user_message_count: 2,
      initial_send_control: null
    }
  });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.status, "controller_active");
  assert.equal(result.retryable, true);
  assert.equal(f.calls.some(call => call[0] === "rollback"), false);
  assert.equal(f.journal.records.size, 0);
});
