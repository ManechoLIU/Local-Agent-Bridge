import test from "node:test";
import assert from "node:assert/strict";
import { RuntimeHostVerifier } from "../attestation.mjs";
import {
  createNativeHostHandler,
  MAX_CONTINUATION_CHARS
} from "./native-host-core.mjs";

const SECRET = "chrome-host-test-secret-0123456789abcdef0123456789abcdef";
const CONVERSATION = "6a9e4158-cc8c-83ea-9763-05a7fd51f597";

function observation(targetId) {
  return {
    host_type: "chatgpt_web",
    browser_target_id: targetId || "browser-target-7",
    top_frame_id: "top-frame",
    loader_id: "loader-current",
    secure_origin: "https://chatgpt.com",
    conversation_id: CONVERSATION,
    observed_at_ms: 1800000000100,
    source: "browser_cdp_page_frame_tree_v1"
  };
}

test("native Host signs browser observation without exposing Host key to browser command", async function () {
  let browserCommand;
  const verifier = new RuntimeHostVerifier(SECRET, {
    clock: function () { return 1800000000200; }
  });
  const challenge = verifier.issueChallenge({
    conversation_id: CONVERSATION,
    target_generation: 4,
    ownership_generation: 8
  });

  const handle = createNativeHostHandler({
    secret: SECRET,
    clock: function () { return 1800000000200; },
    sendBrowserCommand: async function (command) {
      browserCommand = command;
      return {
        request_id: command.request_id,
        ok: true,
        result: { observation: observation() }
      };
    }
  });

  const result = await handle({ op: "attest", challenge: challenge });
  assert.equal(browserCommand.op, "observe_conversation");
  assert.equal(browserCommand.conversation_id, CONVERSATION);
  assert.equal(JSON.stringify(browserCommand).includes(SECRET), false);

  const verified = verifier.verifyAndConsume(result.attestation, {
    conversation_id: CONVERSATION,
    target_generation: 4,
    ownership_generation: 8
  });
  assert.equal(verified.browser_target_id, "browser-target-7");
  assert.equal(verified.top_frame_id, "top-frame");
  assert.equal(verified.loader_id, "loader-current");
});

test("native Host rejects wrong target returned by browser machine boundary", async function () {
  const handle = createNativeHostHandler({
    secret: SECRET,
    sendBrowserCommand: async function (command) {
      return {
        request_id: command.request_id,
        ok: true,
        result: { observation: observation("different-target") }
      };
    }
  });
  await assert.rejects(
    handle({ op: "observe_target", browser_target_id: "expected-target" }),
    /wrong target/
  );
});

test("oversized continuation is rejected before browser dispatch", async function () {
  let calls = 0;
  const handle = createNativeHostHandler({
    secret: SECRET,
    sendBrowserCommand: async function (command) {
      calls += 1;
      return { request_id: command.request_id, ok: true, result: {} };
    }
  });
  await assert.rejects(
    handle({
      op: "submit_continuation",
      browser_target_id: "browser-target-7",
      conversation_id: CONVERSATION,
      payload: "x".repeat(MAX_CONTINUATION_CHARS + 1),
      wake_id: "wake",
      wake_nonce: "nonce",
      target_generation: 4,
      ownership_generation: 8
    }),
    /payload exceeds limit/
  );
  assert.equal(calls, 0);
});

test("submit not implemented remains a confirmed pre-dispatch non-PASS result", async function () {
  const handle = createNativeHostHandler({
    secret: SECRET,
    sendBrowserCommand: async function (command) {
      return {
        request_id: command.request_id,
        ok: true,
        result: {
          dispatch_attempted: false,
          submit_confirmed: false,
          retryable: false,
          status: "submit_not_implemented",
          host_submit_receipt: null
        }
      };
    }
  });
  const result = await handle({
    op: "submit_continuation",
    browser_target_id: "browser-target-7",
    conversation_id: CONVERSATION,
    payload: "resume",
    wake_id: "wake",
    wake_nonce: "nonce",
    target_generation: 4,
    ownership_generation: 8
  });
  assert.deepEqual(result, {
    dispatch_attempted: false,
    submit_confirmed: false,
    retryable: false,
    status: "submit_not_implemented",
    host_submit_receipt: null,
    submit_diagnostics: null
  });
});

test("browser response request id mismatch fails closed", async function () {
  const handle = createNativeHostHandler({
    secret: SECRET,
    sendBrowserCommand: async function () {
      return {
        request_id: "wrong",
        ok: true,
        result: { observation: observation() }
      };
    }
  });
  await assert.rejects(
    handle({ op: "observe_target", browser_target_id: "browser-target-7" }),
    /request_id mismatch/
  );
});

test("health reports live-submit validation state", async function () {
  const handle = createNativeHostHandler({
    secret: SECRET,
    getLiveValidation: function () {
      return { submit_live_validated: true, validated_at_ms: 1800000000300 };
    },
    sendBrowserCommand: async function () {
      throw new Error("browser must not be called for health");
    }
  });
  assert.deepEqual(await handle({ op: "health" }), {
    status: "ready",
    interface_version: 1,
    submit_implemented: true,
    submit_live_validated: true,
    submit_live_validated_at_ms: 1800000000300
  });
});

test("confirmed live submit records validation but uncertain submit does not", async function () {
  const records = [];
  let confirmed = true;
  const handle = createNativeHostHandler({
    secret: SECRET,
    recordLiveValidation: function (record) { records.push(record); },
    sendBrowserCommand: async function (command) {
      return {
        request_id: command.request_id,
        ok: true,
        result: {
          dispatch_attempted: true,
          submit_confirmed: confirmed,
          retryable: false,
          status: confirmed ? "submit_confirmed" : "result_unknown",
          host_submit_receipt: confirmed ? "chrome_submit_live" : null
        }
      };
    }
  });
  const request = {
    op: "submit_continuation",
    browser_target_id: "browser-target-7",
    conversation_id: CONVERSATION,
    payload: "resume",
    wake_id: "wake-live",
    wake_nonce: "nonce-live",
    target_generation: 4,
    ownership_generation: 8
  };
  await handle(request);
  assert.equal(records.length, 1);
  assert.equal(records[0].conversation_id, CONVERSATION);
  assert.equal(records[0].browser_target_id, "browser-target-7");
  assert.equal(records[0].host_submit_receipt, "chrome_submit_live");

  confirmed = false;
  await handle({ ...request, wake_id: "wake-unknown" });
  assert.equal(records.length, 1);
});

test("native Host submit diagnostics are structurally whitelisted and never carry prompt text", async function () {
  const handle = createNativeHostHandler({
    secret: SECRET,
    sendBrowserCommand: async function (command) {
      return {
        request_id: command.request_id,
        ok: true,
        result: {
          dispatch_attempted: true,
          submit_confirmed: true,
          retryable: false,
          status: "submit_confirmed",
          host_submit_receipt: "chrome_submit_diag",
          submit_diagnostics: {
            schema_version: 1,
            wake_id: "wake-diag",
            browser_target_id: "browser-target-7",
            phase: "confirmed",
            debugger_attach: { attempted: true, ok: true },
            composer_preparation: {
              attempted: true,
              ok: true,
              status: "prepared",
              rollback_attempted: false,
              rollback_succeeded: false
            },
            send_control_resolution: {
              attempted: true,
              ok: true,
              selector_kind: "testid_send_button",
              coordinates_changed_since_prepare: true
            },
            trusted_dispatch: {
              attempted: true,
              focus_emulation: true,
              mouse_moved: true,
              mouse_pressed: true,
              mouse_released: true
            },
            confirmation: {
              attempted: true,
              ok: true,
              composer_empty: true,
              exact_new_user_message: true
            },
            foreground: {
              mode: "background_focus_emulation",
              activated_target: false,
              restored_previous_active: false,
              user_changed_focus: false
            },
            failure_stage: null,
            dispatch_attempted: true,
            submit_confirmed: true,
            retryable: false,
            payload: "MUST_NOT_ESCAPE",
            continuation_payload: "MUST_NOT_ESCAPE",
            text: "MUST_NOT_ESCAPE"
          }
        }
      };
    }
  });

  const result = await handle({
    op: "submit_continuation",
    browser_target_id: "browser-target-7",
    conversation_id: CONVERSATION,
    payload: "secret prompt body",
    wake_id: "wake-diag",
    wake_nonce: "nonce-diag",
    target_generation: 4,
    ownership_generation: 8
  });
  assert.equal(result.submit_diagnostics.wake_id, "wake-diag");
  assert.equal(result.submit_diagnostics.debugger_attach.ok, true);
  assert.equal(result.submit_diagnostics.send_control_resolution.coordinates_changed_since_prepare, true);
  assert.equal(result.submit_diagnostics.confirmation.exact_new_user_message, true);
  const serialized = JSON.stringify(result.submit_diagnostics);
  assert.equal(serialized.includes("MUST_NOT_ESCAPE"), false);
  assert.equal(serialized.includes("secret prompt body"), false);
});
