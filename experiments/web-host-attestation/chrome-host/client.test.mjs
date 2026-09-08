import test from "node:test";
import assert from "node:assert/strict";
import net from "node:net";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { requestNativeHost } from "./client.mjs";

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
