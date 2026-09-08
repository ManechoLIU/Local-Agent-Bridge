import test from "node:test";
import assert from "node:assert/strict";
import net from "node:net";
import { mkdtempSync, rmSync } from "node:fs";
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
