import test from "node:test";
import assert from "node:assert/strict";
import net from "node:net";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createNativeHostHandler } from "./native-host-core.mjs";
import { createChromeRuntimeHostIntegration, executeRuntimeVerifierCommand } from "./integration.mjs";
import { signCurrentEntryEvidence } from "../attestation.mjs";

const SECRET = "integration-test-secret-0123456789abcdef0123456789abcdef";
const CONVERSATION = "6a9e4158-cc8c-83ea-9763-05a7fd51f597";

function observation() {
  return {
    host_type: "chatgpt_web",
    browser_target_id: "browser-target-integration",
    top_frame_id: "top-frame-integration",
    loader_id: "loader-integration",
    secure_origin: "https://chatgpt.com",
    conversation_id: CONVERSATION,
    observed_at_ms: 1800000000100,
    source: "browser_cdp_page_frame_tree_v1"
  };
}

test("Runtime-facing Chrome integration issues challenge obtains Host attestation and verifies exact target", async function (t) {
  const root = mkdtempSync(join(tmpdir(), "lab-chrome-integration-"));
  const socketPath = join(root, "host.sock");
  t.after(function () {
    rmSync(root, { recursive: true, force: true });
  });

  const handle = createNativeHostHandler({
    secret: SECRET,
    clock: function () { return 1800000000200; },
    sendBrowserCommand: async function (command) {
      if (command.op === "submit_continuation") {
        return {
          request_id: command.request_id,
          ok: true,
          result: {
            dispatch_attempted: true,
            submit_confirmed: true,
            retryable: false,
            status: "submit_confirmed",
            host_submit_receipt: "chrome_submit_integration"
          }
        };
      }
      return {
        request_id: command.request_id,
        ok: true,
        result: { observation: observation() }
      };
    }
  });

  const server = net.createServer(function (socket) {
    let buffer = "";
    socket.on("data", function (chunk) {
      buffer += chunk.toString("utf8");
      const newline = buffer.indexOf("\n");
      if (newline < 0) return;
      const request = JSON.parse(buffer.slice(0, newline));
      handle(request).then(function (result) {
        socket.end(JSON.stringify({
          request_id: request.request_id,
          ok: true,
          result: result
        }) + "\n");
      }).catch(function (error) {
        socket.end(JSON.stringify({
          request_id: request.request_id,
          ok: false,
          error: error.message
        }) + "\n");
      });
    });
  });
  await new Promise(function (resolve, reject) {
    server.once("error", reject);
    server.listen(socketPath, resolve);
  });
  t.after(function () {
    server.close();
  });

  const integration = createChromeRuntimeHostIntegration({
    secret: SECRET,
    socketPath: socketPath,
    clock: function () { return 1800000000200; }
  });

  assert.deepEqual(integration.capabilities, [
    "issueChallenge",
    "requestAttestation",
    "verifyAttestation",
    "discoverCurrentEntry",
    "attestAndVerify",
    "consumeVerifiedTarget",
    "submitReentry",
    "submitAndVerifyReentry",
    "verifyToolPreReceipt",
    "verifyToolTerminalReceipt"
  ]);

  const issued = integration.issueChallenge({
    conversation_id: CONVERSATION,
    target_generation: 6,
    ownership_generation: 10
  });
  const attestation = await integration.requestAttestation(issued);
  const verified = integration.verifyAttestation(attestation, {
    conversation_id: CONVERSATION,
    target_generation: 6,
    ownership_generation: 10
  });
  assert.equal(verified.browser_target_id, "browser-target-integration");

  const result = await integration.attestAndVerify({
    conversation_id: CONVERSATION,
    target_generation: 7,
    ownership_generation: 11
  });
  assert.equal(result.verified_target.conversation_id, CONVERSATION);
  assert.equal(result.verified_target.browser_target_id, "browser-target-integration");
  assert.equal(result.verified_target.target_generation, 7);
  assert.equal(result.verified_target.ownership_generation, 11);

  const submitted = await integration.submitAndVerifyReentry({
    host_receipt_id: result.host_receipt_id,
    conversation_id: CONVERSATION,
    wake_id: "wake-integration-full",
    wake_nonce: "nonce-integration-full",
    continuation_payload: "continue exact controller",
    expected_target_generation: 7,
    expected_ownership_generation: 11
  });
  assert.equal(submitted.result_class, "SUBMIT_CONFIRMED");
  assert.equal(submitted.browser_target_id, "browser-target-integration");
  assert.equal(submitted.wake_id, "wake-integration-full");
  assert.equal(submitted.target_generation, 7);
  assert.equal(submitted.ownership_generation, 11);
});

test("Runtime verifier command boundary returns only machine-verifiable attestation result", async function () {
  const module = await import("./integration.mjs");
  assert.equal(typeof module.executeRuntimeVerifierCommand, "function");
  const calls = [];
  const result = await module.executeRuntimeVerifierCommand({
    operation: "attest_and_verify",
    conversation_id: CONVERSATION,
    target_generation: 12,
    ownership_generation: 13
  }, {
    integration: {
      async attestAndVerify(input) {
        calls.push(input);
        return {
          challenge_id: "hc_runtime",
          host_receipt_id: "hr_runtime",
          verified_target: {
            provenance: "runtime_host_verifier_v1",
            conversation_id: CONVERSATION,
            browser_target_id: "browser-runtime",
            top_frame_id: "top-runtime",
            loader_id: "loader-runtime",
            secure_origin: "https://chatgpt.com",
            target_generation: 12,
            ownership_generation: 13
          }
        };
      }
    }
  });
  assert.deepEqual(calls, [{
    conversation_id: CONVERSATION,
    target_generation: 12,
    ownership_generation: 13
  }]);
  assert.deepEqual(result, {
    ok: true,
    operation: "attest_and_verify",
    challenge_id: "hc_runtime",
    host_receipt_id: "hr_runtime",
    verified_target: {
      provenance: "runtime_host_verifier_v1",
      conversation_id: CONVERSATION,
      browser_target_id: "browser-runtime",
      top_frame_id: "top-runtime",
      loader_id: "loader-runtime",
      secure_origin: "https://chatgpt.com",
      target_generation: 12,
      ownership_generation: 13
    }
  });
});

test("Runtime verifier command boundary submits only by one-time Host receipt reference", async function () {
  const module = await import("./integration.mjs");
  const calls = [];
  const result = await module.executeRuntimeVerifierCommand({
    operation: "submit_reentry",
    conversation_id: CONVERSATION,
    host_receipt_id: "hr_runtime_submit",
    expected_target_generation: 14,
    expected_ownership_generation: 15,
    wake_id: "wake-runtime-submit",
    wake_nonce: "nonce-runtime-submit",
    continuation_payload: "continue the existing controller"
  }, {
    integration: {
      async submitAndVerifyReentry(input) {
        calls.push(input);
        return {
          provenance: "browser_host_reentry_receipt_v1",
          wake_id: input.wake_id,
          conversation_id: CONVERSATION,
          target_generation: 14,
          ownership_generation: 15,
          dispatch_attempted: true,
          submit_confirmed: true,
          retryable: false,
          auto_retry_allowed: false,
          result_class: "SUBMIT_CONFIRMED",
          status: "submit_confirmed",
          receipt_id: "wr_runtime_submit"
        };
      }
    }
  });
  assert.deepEqual(calls, [{
    host_receipt_id: "hr_runtime_submit",
    conversation_id: CONVERSATION,
    wake_id: "wake-runtime-submit",
    wake_nonce: "nonce-runtime-submit",
    continuation_payload: "continue the existing controller",
    expected_target_generation: 14,
    expected_ownership_generation: 15
  }]);
  assert.equal(result.ok, true);
  assert.equal(result.operation, "submit_reentry");
  assert.equal(result.reentry_receipt.result_class, "SUBMIT_CONFIRMED");
});

test("Runtime verifier command boundary rejects caller-supplied verified target shortcut", async function () {
  const module = await import("./integration.mjs");
  let called = false;
  await assert.rejects(
    module.executeRuntimeVerifierCommand({
      operation: "submit_reentry",
      conversation_id: CONVERSATION,
      verified_target: {
        provenance: "runtime_host_verifier_v1",
        conversation_id: CONVERSATION,
        target_generation: 14,
        ownership_generation: 15
      },
      expected_target_generation: 14,
      expected_ownership_generation: 15,
      wake_id: "wake-stale",
      wake_nonce: "nonce-stale",
      continuation_payload: "continue"
    }, {
      integration: {
        async submitAndVerifyReentry() {
          called = true;
          return {};
        }
      }
    }),
    /host_receipt_id is required/
  );
  assert.equal(called, false);
});

test("Runtime verifier command boundary rejects caller-supplied attestation shortcuts", async function () {
  const module = await import("./integration.mjs");
  assert.equal(typeof module.executeRuntimeVerifierCommand, "function");
  await assert.rejects(
    module.executeRuntimeVerifierCommand({
      operation: "verify_attestation",
      attestation: { host_mac_sha256: "caller-controlled" }
    }, { integration: {} }),
    /unsupported Runtime Host operation/
  );
});

test("Runtime verifier CLI rejects unsupported caller-controlled verification operations", async function () {
  const { spawn } = await import("node:child_process");
  const { once } = await import("node:events");
  const child = spawn(process.execPath, ["./chrome-host/runtime-verifier-cli.mjs"], {
    cwd: new URL("..", import.meta.url).pathname,
    stdio: ["pipe", "pipe", "pipe"]
  });
  let stdout = "";
  child.stdout.on("data", (chunk) => { stdout += chunk.toString("utf8"); });
  child.stdin.end(JSON.stringify({ operation: "verify_attestation", attestation: { host_mac_sha256: "caller" } }) + "\n");
  const [code] = await once(child, "exit");
  assert.equal(code, 1);
  const payload = JSON.parse(stdout.trim());
  assert.equal(payload.ok, false);
  assert.equal(payload.error_code, "RUNTIME_HOST_VERIFIER_FAILED");
  assert.match(payload.error, /unsupported Runtime Host operation/);
});


test("Runtime verifier CLI process performs fresh challenge Host request and exact verification", async function (t) {
  const { spawn } = await import("node:child_process");
  const { once } = await import("node:events");
  const root = mkdtempSync(join(tmpdir(), "lab-runtime-verifier-cli-"));
  const socketPath = join(root, "host.sock");
  const keyPath = join(root, "attestation.key");
  writeFileSync(keyPath, SECRET, { mode: 0o600 });
  chmodSync(keyPath, 0o600);
  t.after(function () { rmSync(root, { recursive: true, force: true }); });

  const handle = createNativeHostHandler({
    secret: SECRET,
    sendBrowserCommand: async function (command) {
      return {
        request_id: command.request_id,
        ok: true,
        result: { observation: { ...observation(), observed_at_ms: Date.now() } }
      };
    }
  });
  const server = net.createServer(function (socket) {
    let buffer = "";
    socket.on("data", function (chunk) {
      buffer += chunk.toString("utf8");
      const newline = buffer.indexOf("\n");
      if (newline < 0) return;
      const request = JSON.parse(buffer.slice(0, newline));
      handle(request).then(function (result) {
        socket.end(JSON.stringify({ request_id: request.request_id, ok: true, result }) + "\n");
      }).catch(function (error) {
        socket.end(JSON.stringify({ request_id: request.request_id, ok: false, error: error.message }) + "\n");
      });
    });
  });
  await new Promise(function (resolve, reject) {
    server.once("error", reject);
    server.listen(socketPath, resolve);
  });
  t.after(function () { server.close(); });

  const child = spawn(process.execPath, ["./chrome-host/runtime-verifier-cli.mjs"], {
    cwd: new URL("..", import.meta.url).pathname,
    env: { ...process.env, LAB_HOST_ATTESTATION_KEY_FILE: keyPath, LAB_HOST_ADAPTER_SOCKET: socketPath },
    stdio: ["pipe", "pipe", "pipe"]
  });
  let stdout = "";
  child.stdout.on("data", (chunk) => { stdout += chunk.toString("utf8"); });
  child.stdin.end(JSON.stringify({
    operation: "attest_and_verify",
    conversation_id: CONVERSATION,
    target_generation: 21,
    ownership_generation: 34
  }) + "\n");
  const [code] = await once(child, "exit");
  assert.equal(code, 0);
  const payload = JSON.parse(stdout.trim());
  assert.equal(payload.ok, true);
  assert.equal(payload.verified_target.conversation_id, CONVERSATION);
  assert.equal(payload.verified_target.target_generation, 21);
  assert.equal(payload.verified_target.ownership_generation, 34);
  assert.equal(payload.verified_target.provenance, "runtime_host_verifier_v1");
});

test("Runtime verifier verifies signed current-entry receipt without re-attesting browser generation and rejects replay", async function () {
  const root = mkdtempSync(join(tmpdir(), "lab-current-entry-consume-"));
  const statePath = join(root, "verifier-state.json");
  try {
    const clock = () => 1800000000200;
    const currentEntry = signCurrentEntryEvidence({
      secret: SECRET,
      machineContext: {
        entry_scope: "runtime_invocation",
        machine_source: "host_invocation_context_v1",
        conversation_id: CONVERSATION,
        browser_target_id: "browser-current-consume",
        top_frame_id: "frame-current-consume",
        loader_id: "loader-current-consume",
        secure_origin: "https://chatgpt.com",
        generation_anchor_sha256: "a".repeat(64),
        observed_at_ms: 1800000000100
      },
      targetGeneration: 21,
      ownershipGeneration: 34,
      clock
    });
    const integration = createChromeRuntimeHostIntegration({
      secret: SECRET,
      clock,
      verifierStatePath: statePath,
      driver: {
        async attest() { throw new Error("browser re-attestation must not run"); },
        async discoverCurrentEntry() { throw new Error("browser rediscovery must not run"); }
      }
    });
    const request = {
      operation: "verify_current_entry",
      conversation_id: CONVERSATION,
      target_generation: 21,
      ownership_generation: 34,
      current_entry: currentEntry
    };
    const result = await executeRuntimeVerifierCommand(request, { integration });
    assert.equal(result.ok, true);
    assert.equal(result.operation, "verify_current_entry");
    assert.equal(result.host_receipt_id, currentEntry.host_receipt_id);
    assert.equal(result.verified_target.provenance, "runtime_host_verifier_v1");
    assert.equal(result.verified_target.conversation_id, CONVERSATION);
    assert.equal(result.verified_target.target_generation, 21);
    assert.equal(result.verified_target.ownership_generation, 34);
    await assert.rejects(
      executeRuntimeVerifierCommand(request, { integration }),
      /receipt replayed/i
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("Runtime verifier discover_current_entry never accepts caller conversation and returns Host machine entry", async function () {
  const module = await import("./integration.mjs");
  const calls = [];
  const result = await module.executeRuntimeVerifierCommand({
    operation: "discover_current_entry",
    controller_id: "controller-existing",
    target_generation: 21,
    ownership_generation: 34
  }, {
    integration: {
      async discoverCurrentEntry(input) {
        calls.push(input);
        return {
          provenance: "runtime_host_current_entry_v1",
          entry_scope: "runtime_invocation",
          machine_source: "host_invocation_context_v1",
          conversation_id: CONVERSATION,
          browser_target_id: "browser-current-runtime",
          top_frame_id: "frame-current-runtime",
          loader_id: "loader-current-runtime",
          secure_origin: "https://chatgpt.com",
          target_generation: 21,
          ownership_generation: 34,
          host_receipt_id: "hce_runtime",
          observed_at_unix_ms: 1800000000200
        };
      }
    }
  });
  assert.deepEqual(calls, [{ target_generation: 21, ownership_generation: 34 }]);
  assert.equal(result.ok, true);
  assert.equal(result.operation, "discover_current_entry");
  assert.equal(result.current_entry.conversation_id, CONVERSATION);
  assert.equal(result.current_entry.provenance, "runtime_host_current_entry_v1");

  await assert.rejects(
    module.executeRuntimeVerifierCommand({
      operation: "discover_current_entry",
      conversation_id: CONVERSATION,
      target_generation: 21,
      ownership_generation: 34
    }, { integration: { async discoverCurrentEntry() { return {}; } } }),
    /caller.*conversation|conversation.*not.*accepted/i
  );
});
