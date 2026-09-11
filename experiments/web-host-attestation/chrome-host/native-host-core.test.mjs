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

test("extension readiness callback fails closed before every runtime operation", async function () {
  let browserCalls = 0;
  const handle = createNativeHostHandler({
    secret: SECRET,
    requireExtensionReady: function () {
      throw new Error("HOST_EXTENSION_NOT_READY: valid extension hello required");
    },
    sendBrowserCommand: async function () {
      browserCalls += 1;
      throw new Error("browser must not be called before extension readiness");
    }
  });
  const requests = [
    { op: "health" },
    { op: "discover_current_entry", target_generation: 4, ownership_generation: 8 },
    { op: "attest", challenge: {} },
    { op: "observe_target", browser_target_id: "browser-target-7" },
    {
      op: "submit_continuation",
      browser_target_id: "browser-target-7",
      conversation_id: CONVERSATION,
      payload: "resume",
      wake_id: "wake",
      wake_nonce: "nonce",
      target_generation: 4,
      ownership_generation: 8
    }
  ];
  for (const request of requests) {
    await assert.rejects(handle(request), /EXTENSION_NOT_READY/);
  }
  assert.equal(browserCalls, 0);
});

test("health exposes only hashed loaded extension epoch summary", async function () {
  const handle = createNativeHostHandler({
    secret: SECRET,
    requireExtensionReady: function () {
      return {
        instance_id: "ei_00000000-0000-4000-8000-000000000002",
        instance_id_sha256: "a".repeat(64),
        loaded_at_ms: 1800000000200,
        binding_sha256: "b".repeat(64),
        binding_path: "/private/extension-binding.json"
      };
    },
    sendBrowserCommand: async function () {
      throw new Error("browser must not be called for health");
    }
  });
  const health = await handle({ op: "health" });
  assert.equal(health.extension_ready, true);
  assert.equal(health.extension_instance_id_sha256, "a".repeat(64));
  assert.equal(health.extension_loaded_at_ms, 1800000000200);
  assert.equal(health.extension_binding_sha256, "b".repeat(64));
  assert.equal(JSON.stringify(health).includes("/private/extension-binding.json"), false);
  assert.equal(JSON.stringify(health).includes("ei_00000000-0000-4000-8000-000000000002"), false);
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

test("current-entry discovery rejects caller conversation and requires browser machine invocation context", async function () {
  let calls = 0;
  const handle = createNativeHostHandler({
    secret: SECRET,
    clock: function () { return 1800000000200; },
    sendBrowserCommand: async function (command) {
      calls += 1;
      return { request_id: command.request_id, ok: false, error: "HOST_CURRENT_ENTRY_UNAVAILABLE: no active ChatGPT generation" };
    }
  });
  await assert.rejects(
    handle({ op: "discover_current_entry", conversation_id: CONVERSATION, target_generation: 4, ownership_generation: 8 }),
    /caller.*conversation|conversation.*not.*accepted/i
  );
  assert.equal(calls, 0);
  await assert.rejects(
    handle({ op: "discover_current_entry", target_generation: 4, ownership_generation: 8 }),
    /CURRENT_ENTRY_UNAVAILABLE|active ChatGPT generation/i
  );
  assert.equal(calls, 1);
});

test("private receipt operations derive browser identity and produce signed capability, pre, and terminal receipts", async function () {
  const epoch = {
    instance_id_sha256: "a".repeat(64),
    loaded_at_ms: 1800000000200,
    binding_sha256: "b".repeat(64)
  };
  const handle = createNativeHostHandler({
    secret: SECRET,
    requireExtensionReady: () => epoch,
    sendBrowserCommand: async function (command) {
      return {
        request_id: command.request_id,
        ok: true,
        result: {
          current_entry_context: {
            entry_scope: "runtime_invocation",
            machine_source: "host_invocation_context_v1",
            conversation_id: CONVERSATION,
            browser_target_id: "browser-target-7",
            top_frame_id: "top-frame",
            loader_id: "loader-current",
            secure_origin: "https://chatgpt.com",
            generation_anchor_sha256: "c".repeat(64),
            observed_at_ms: 1800000000200
          }
        }
      };
    }
  });
  const request = {
    bridge_call_id: "bc_receipt",
    bridge_challenge_nonce: "bridge-challenge-nonce-test",
    transport_binding_id: "tb_receipt",
    workspace: "/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge",
    production_release_revision: "a".repeat(40),
    production_endpoint_generation: 7,
    bridge_principal_id: "bridge-principal-test",
    bridge_execution_session_id: "bridge-execution-session-test",
    bridge_capability_id: "bridge-capability-test",
    bridge_workspace_id: "local-agent-bridge-production",
    tool_name: "run_command",
    outer_request_sha256: "d".repeat(64),
    forwarded_request_sha256: "e".repeat(64),
    normalized_request_sha256: "f".repeat(64)
  };
  await assert.rejects(
    handle({ op: "tool_receipt_issue", ...request, conversation_id: "caller-controlled" }),
    /caller machine identity/i
  );
  const issued = await handle({ op: "tool_receipt_issue", ...request });
  assert.equal(issued.capability.provenance, "lab_host_invocation_capability_v1");
  assert.equal(issued.capability.conversation_id, CONVERSATION);
  const consumed = await handle({
    op: "tool_receipt_consume", ...request, capability: issued.capability, host_tool_execution_id: "hte_receipt"
  });
  assert.equal(consumed.pre_receipt.provenance, "lab_host_tool_pre_receipt_v1");
  const terminal = await handle({
    op: "tool_receipt_terminal", ...request, pre_receipt: consumed.pre_receipt,
    backend_route: "/v1/tools/run_command", http_status: 200,
    response_body_sha256: "1".repeat(64), terminal_classification: "SUCCESS"
  });
  assert.equal(terminal.terminal_receipt.provenance, "lab_host_tool_terminal_receipt_v1");
});

test("current-entry discovery signs only browser-provided runtime-invocation machine context", async function () {
  let browserCommand = null;
  const handle = createNativeHostHandler({
    secret: SECRET,
    clock: function () { return 1800000000200; },
    sendBrowserCommand: async function (command) {
      browserCommand = command;
      return {
        request_id: command.request_id,
        ok: true,
        result: {
          current_entry_context: {
            entry_scope: "runtime_invocation",
            machine_source: "host_invocation_context_v1",
            conversation_id: CONVERSATION,
            browser_target_id: "browser-target-current-entry",
            top_frame_id: "top-frame-current-entry",
            loader_id: "loader-current-entry",
            secure_origin: "https://chatgpt.com",
            observed_at_ms: 1800000000150
          }
        }
      };
    }
  });
  const result = await handle({ op: "discover_current_entry", target_generation: 4, ownership_generation: 8 });
  assert.equal(browserCommand.op, "discover_current_entry");
  assert.equal(Object.prototype.hasOwnProperty.call(browserCommand, "conversation_id"), false);
  assert.equal(result.current_entry.provenance, "runtime_host_current_entry_v1");
  assert.equal(result.current_entry.entry_scope, "runtime_invocation");
  assert.equal(result.current_entry.machine_source, "host_invocation_context_v1");
  assert.equal(result.current_entry.conversation_id, CONVERSATION);
  assert.equal(result.current_entry.browser_target_id, "browser-target-current-entry");
  assert.equal(result.current_entry.top_frame_id, "top-frame-current-entry");
  assert.equal(result.current_entry.loader_id, "loader-current-entry");
  assert.equal(result.current_entry.secure_origin, "https://chatgpt.com");
  assert.equal(result.current_entry.target_generation, 4);
  assert.equal(result.current_entry.ownership_generation, 8);
  assert.match(result.current_entry.host_receipt_id, /^hce_/);
  assert.equal(result.current_entry.observed_at_unix_ms, 1800000000150);
  assert.match(result.current_entry.host_mac_sha256, /^[0-9a-f]{64}$/);
});
