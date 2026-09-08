import test from "node:test";
import assert from "node:assert/strict";
import net from "node:net";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createNativeHostHandler } from "./native-host-core.mjs";
import { createChromeRuntimeHostIntegration } from "./integration.mjs";

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
    "attestAndVerify",
    "submitReentry"
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
