import test from "node:test";
import assert from "node:assert/strict";
import net from "node:net";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createChromeHostDriver, requestNativeHost } from "./client.mjs";

async function serverFor(t, responder) {
  const root = mkdtempSync(join(tmpdir(), "lab-client-test-"));
  const socketPath = join(root, "host.sock");
  const server = net.createServer(function (socket) {
    let buffer = "";
    socket.on("data", function (chunk) {
      buffer += chunk.toString("utf8");
      const newline = buffer.indexOf("\n");
      if (newline < 0) return;
      const request = JSON.parse(buffer.slice(0, newline));
      const response = responder(request);
      socket.end(JSON.stringify(response) + "\n");
    });
  });
  await new Promise(function (resolve, reject) {
    server.once("error", reject);
    server.listen(socketPath, resolve);
  });
  t.after(function () {
    server.close();
    rmSync(root, { recursive: true, force: true });
  });
  return socketPath;
}

test("Unix socket client rejects response request id mismatch", async function (t) {
  const socketPath = await serverFor(t, function () {
    return {
      request_id: "wrong-request",
      ok: true,
      result: { status: "ready" }
    };
  });
  await assert.rejects(
    requestNativeHost(socketPath, { op: "health" }, { timeoutMs: 1000 }),
    /request_id mismatch/
  );
});

test("Unix socket client accepts only matching request id", async function (t) {
  const socketPath = await serverFor(t, function (request) {
    return {
      request_id: request.request_id,
      ok: true,
      result: { status: "ready" }
    };
  });
  const result = await requestNativeHost(socketPath, { op: "health" }, { timeoutMs: 1000 });
  assert.deepEqual(result, { status: "ready" });
});

test("driver rejects a stale native Host extension binding epoch", async function (t) {
  const expected = "a".repeat(64);
  const socketPath = await serverFor(t, function (request) {
    return {
      request_id: request.request_id,
      ok: true,
      result: {
        current_entry: { conversation_id: "stale" },
        extension_epoch: {
          binding_sha256: "b".repeat(64),
          instance_id_sha256: "c".repeat(64),
          loaded_at_ms: 1
        }
      }
    };
  });
  const driver = createChromeHostDriver({
    socketPath,
    timeoutMs: 1000,
    expectedExtensionBindingSha256: expected
  });
  await assert.rejects(
    driver.discoverCurrentEntry({ target_generation: 1, ownership_generation: 1 }),
    /extension epoch does not match current binding/
  );
});

test("driver accepts only the current native Host extension binding epoch", async function (t) {
  const expected = "a".repeat(64);
  const socketPath = await serverFor(t, function (request) {
    return {
      request_id: request.request_id,
      ok: true,
      result: {
        current_entry: { conversation_id: "current" },
        extension_epoch: {
          binding_sha256: expected,
          instance_id_sha256: "c".repeat(64),
          loaded_at_ms: 1
        }
      }
    };
  });
  const driver = createChromeHostDriver({
    socketPath,
    timeoutMs: 1000,
    expectedExtensionBindingSha256: expected
  });
  assert.deepEqual(
    await driver.discoverCurrentEntry({ target_generation: 1, ownership_generation: 1 }),
    { conversation_id: "current" }
  );
  assert.deepEqual(
    await driver.discoverCurrentEntryWithEpoch({ target_generation: 1, ownership_generation: 1 }),
    { current_entry: { conversation_id: "current" }, extension_epoch: { binding_sha256: expected, instance_id_sha256: "c".repeat(64), loaded_at_ms: 1 } }
  );
});
