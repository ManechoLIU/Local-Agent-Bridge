import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import {
  chmodSync,
  existsSync,
  mkdtempSync,
  rmSync,
  writeFileSync
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  RuntimeHostVerifier
} from "../attestation.mjs";
import {
  requestNativeHost
} from "./client.mjs";
import {
  consumeNativeFrames,
  encodeNativeMessage
} from "./native-framing.mjs";

const SECRET = "native-process-test-secret-0123456789abcdef0123456789abcdef";
const CONVERSATION = "6a9e4158-cc8c-83ea-9763-05a7fd51f597";

function waitForSocket(path) {
  return new Promise(function (resolve, reject) {
    let remaining = 50;
    const timer = setInterval(async function () {
      try {
        const net = await import("node:net");
        const socket = net.createConnection({ path: path });
        socket.once("connect", function () {
          socket.destroy();
          clearInterval(timer);
          resolve();
        });
        socket.once("error", function () {
          socket.destroy();
          remaining -= 1;
          if (remaining <= 0) {
            clearInterval(timer);
            reject(new Error("native Host socket did not become ready"));
          }
        });
      } catch (error) {
        clearInterval(timer);
        reject(error);
      }
    }, 20);
  });
}

test("real native Host process bridges Unix socket to Native Messaging and signs attestation", async function (t) {
  const root = mkdtempSync(join(tmpdir(), "lab-native-host-"));
  const keyPath = join(root, "attestation.key");
  const socketPath = join(root, "host.sock");
  writeFileSync(keyPath, SECRET, { mode: 0o600 });
  chmodSync(keyPath, 0o600);

  const child = spawn(process.execPath, ["./chrome-host/native-host.mjs"], {
    cwd: new URL("..", import.meta.url).pathname,
    env: Object.assign({}, process.env, {
      LAB_HOST_ATTESTATION_KEY_FILE: keyPath,
      LAB_HOST_ADAPTER_SOCKET: socketPath
    }),
    stdio: ["pipe", "pipe", "pipe"]
  });

  t.after(function () {
    try { child.kill("SIGTERM"); } catch {}
    rmSync(root, { recursive: true, force: true });
  });

  let stdoutBuffer = Buffer.alloc(0);
  let stderr = "";
  let resolveHelloAck;
  const helloAck = new Promise(function (resolve) {
    resolveHelloAck = resolve;
  });
  child.stderr.on("data", function (chunk) {
    stderr += chunk.toString("utf8");
  });

  child.stdout.on("data", function (chunk) {
    stdoutBuffer = Buffer.concat([stdoutBuffer, chunk]);
    const parsed = consumeNativeFrames(stdoutBuffer);
    stdoutBuffer = parsed.remainder;
    for (const command of parsed.messages) {
      if (command.kind === "extension_hello_ack" && command.request_id === "eh_test") {
        resolveHelloAck(command);
        continue;
      }
      if (command.op === "observe_conversation") {
        child.stdin.write(encodeNativeMessage({
          request_id: command.request_id,
          ok: true,
          result: {
            observation: {
              host_type: "chatgpt_web",
              browser_target_id: "browser-target-process",
              top_frame_id: "top-frame-process",
              loader_id: "loader-process",
              secure_origin: "https://chatgpt.com",
              conversation_id: CONVERSATION,
              observed_at_ms: Date.now(),
              source: "browser_cdp_page_frame_tree_v1"
            }
          }
        }));
        continue;
      }
      if (command.op === "submit_continuation") {
        child.stdin.write(encodeNativeMessage({
          request_id: command.request_id,
          ok: true,
          result: {
            dispatch_attempted: true,
            submit_confirmed: true,
            retryable: false,
            status: "submit_confirmed",
            host_submit_receipt: "chrome_submit_process"
          }
        }));
      }
    }
  });

  await waitForSocket(socketPath);

  child.stdin.write(encodeNativeMessage({
    request_id: "eh_test",
    op: "extension_hello",
    interface_version: 1,
    extension_id: "ikidefdlcddfomimpckflbajjccnmfna"
  }));
  const hello = await Promise.race([
    helloAck,
    new Promise(function (_, reject) {
      setTimeout(function () {
        reject(new Error("extension hello acknowledgement timed out"));
      }, 2000);
    })
  ]);
  assert.deepEqual(hello, {
    kind: "extension_hello_ack",
    request_id: "eh_test",
    ok: true,
    result: {
      status: "native_host_ready",
      interface_version: 1,
      socket_path: socketPath
    }
  });

  const verifier = new RuntimeHostVerifier(SECRET);
  const challenge = verifier.issueChallenge({
    conversation_id: CONVERSATION,
    target_generation: 5,
    ownership_generation: 9
  });

  const result = await requestNativeHost(socketPath, {
    op: "attest",
    challenge: challenge
  }, { timeoutMs: 3000 });

  const verified = verifier.verifyAndConsume(result.attestation, {
    conversation_id: CONVERSATION,
    target_generation: 5,
    ownership_generation: 9
  });
  assert.equal(verified.browser_target_id, "browser-target-process");
  assert.equal(verified.top_frame_id, "top-frame-process");
  assert.equal(verified.loader_id, "loader-process");

  const submit = await requestNativeHost(socketPath, {
    op: "submit_continuation",
    browser_target_id: "browser-target-process",
    conversation_id: CONVERSATION,
    payload: "resume",
    wake_id: "wake-process",
    wake_nonce: "nonce-process",
    target_generation: 4,
    ownership_generation: 8
  }, { timeoutMs: 3000 });
  assert.equal(submit.submit_confirmed, true);

  const health = await requestNativeHost(socketPath, { op: "health" }, { timeoutMs: 3000 });
  assert.equal(health.submit_live_validated, true);
  assert.equal(Number.isSafeInteger(health.submit_live_validated_at_ms), true);
  assert.equal(stderr, "");

  child.stdin.end();
  const exit = await once(child, "exit");
  assert.equal(exit[0], 0);
  assert.equal(existsSync(socketPath), false);
});
