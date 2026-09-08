#!/usr/bin/env node
import net from "node:net";
import {
  chmodSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  rmSync,
  statSync
} from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import {
  createNativeHostHandler,
  MAX_RUNTIME_MESSAGE_BYTES
} from "./native-host-core.mjs";
import {
  consumeNativeFrames,
  encodeNativeMessage,
  MAX_NATIVE_FRAME_BYTES
} from "./native-framing.mjs";
import { BROWSER_MACHINE_COMMAND_TIMEOUT_MS } from "./extension/timeout-budget.js";

const defaultRoot = join(homedir(), ".local-agent-bridge", "web-host");
const socketPath = process.env.LAB_HOST_ADAPTER_SOCKET || join(defaultRoot, "host.sock");
const keyPath = process.env.LAB_HOST_ATTESTATION_KEY_FILE || join(defaultRoot, "attestation.key");

function currentUid() {
  return typeof process.getuid === "function" ? process.getuid() : null;
}

function ensurePrivateDirectory(path) {
  if (!existsSync(path)) mkdirSync(path, { recursive: true, mode: 0o700 });
  const stat = lstatSync(path);
  if (!stat.isDirectory() || stat.isSymbolicLink()) {
    throw new Error("Host state directory must be a real directory");
  }
  const uid = currentUid();
  if (uid != null && stat.uid !== uid) throw new Error("Host state directory owner mismatch");
  chmodSync(path, 0o700);
}

function readPrivateKey(path) {
  const stat = lstatSync(path);
  if (!stat.isFile() || stat.isSymbolicLink()) {
    throw new Error("Host key must be a regular non-symlink file");
  }
  const uid = currentUid();
  if (uid != null && stat.uid !== uid) throw new Error("Host key owner mismatch");
  if ((stat.mode & 0o077) !== 0) throw new Error("Host key permissions must be 0600 or stricter");
  const key = readFileSync(path);
  if (key.length < 32) throw new Error("Host key must contain at least 32 bytes");
  return key;
}

function prepareSocket(path) {
  const parent = dirname(path);
  ensurePrivateDirectory(parent);
  if (!existsSync(path)) return;
  const existing = lstatSync(path);
  if (!existing.isSocket()) throw new Error("existing Host adapter path is not a Unix socket");
  const uid = currentUid();
  if (uid != null && existing.uid !== uid) throw new Error("existing Host socket owner mismatch");
  rmSync(path);
}

ensurePrivateDirectory(dirname(keyPath));
const secret = readPrivateKey(keyPath);
prepareSocket(socketPath);

let nativeInput = Buffer.alloc(0);
const pendingBrowser = new Map();

function sendNative(value) {
  process.stdout.write(encodeNativeMessage(value));
}

function sendBrowserCommand(message) {
  return new Promise(function (resolve, reject) {
    const requestId = message.request_id;
    const timeout = setTimeout(function () {
      pendingBrowser.delete(requestId);
      reject(new Error("browser machine command timed out"));
    }, BROWSER_MACHINE_COMMAND_TIMEOUT_MS);
    pendingBrowser.set(requestId, {
      resolve: resolve,
      reject: reject,
      timeout: timeout
    });
    try {
      sendNative(message);
    } catch (error) {
      clearTimeout(timeout);
      pendingBrowser.delete(requestId);
      reject(error);
    }
  });
}

function consumeBrowserInput(chunk) {
  nativeInput = Buffer.concat([nativeInput, chunk]);
  if (nativeInput.length > MAX_NATIVE_FRAME_BYTES * 2) {
    throw new Error("native input buffer exceeds limit");
  }
  const parsed = consumeNativeFrames(nativeInput);
  nativeInput = parsed.remainder;
  for (const message of parsed.messages) {
    const requestId = String(message && message.request_id || "");
    if (message?.op === "extension_hello") {
      sendNative({
        kind: "extension_hello_ack",
        request_id: requestId,
        ok: true,
        result: {
          status: "native_host_ready",
          interface_version: 1,
          socket_path: socketPath
        }
      });
      continue;
    }
    const pending = pendingBrowser.get(requestId);
    if (!pending) continue;
    clearTimeout(pending.timeout);
    pendingBrowser.delete(requestId);
    pending.resolve(message);
  }
}

process.stdin.on("data", function (chunk) {
  try {
    consumeBrowserInput(chunk);
  } catch {
    process.stderr.write("Local Agent Bridge Host protocol error\n");
    process.exitCode = 1;
  }
});

process.stdin.on("end", function () {
  cleanup();
  process.exit(0);
});

let liveValidationState = null;

const handleRuntimeRequest = createNativeHostHandler({
  secret: secret,
  sendBrowserCommand: sendBrowserCommand,
  getLiveValidation: function () { return liveValidationState; },
  recordLiveValidation: function (record) {
    liveValidationState = Object.freeze({ ...record });
  }
});

const server = net.createServer(function (socket) {
  let buffer = Buffer.alloc(0);
  let handled = false;

  socket.on("data", function (chunk) {
    if (handled) return;
    buffer = Buffer.concat([buffer, chunk]);
    if (buffer.length > MAX_RUNTIME_MESSAGE_BYTES) {
      handled = true;
      socket.end(JSON.stringify({ ok: false, error: "runtime request exceeds limit" }) + "\n");
      return;
    }
    const newline = buffer.indexOf(0x0a);
    if (newline < 0) return;
    handled = true;
    let request;
    try {
      request = JSON.parse(buffer.subarray(0, newline).toString("utf8"));
    } catch {
      socket.end(JSON.stringify({ ok: false, error: "runtime request is not valid JSON" }) + "\n");
      return;
    }

    handleRuntimeRequest(request).then(function (result) {
      socket.end(JSON.stringify({
        request_id: request.request_id || null,
        ok: true,
        result: result
      }) + "\n");
    }).catch(function (error) {
      socket.end(JSON.stringify({
        request_id: request.request_id || null,
        ok: false,
        error: String(error && error.message || error || "Host adapter request failed")
      }) + "\n");
    });
  });
});

server.listen(socketPath, function () {
  chmodSync(socketPath, 0o600);
  const stat = statSync(socketPath);
  if (!stat.isSocket() || (stat.mode & 0o077) !== 0) {
    throw new Error("Host adapter socket permissions are unsafe");
  }
});

function cleanup() {
  try { server.close(); } catch {}
  try { if (existsSync(socketPath)) rmSync(socketPath); } catch {}
}

process.on("SIGINT", function () {
  cleanup();
  process.exit(0);
});
process.on("SIGTERM", function () {
  cleanup();
  process.exit(0);
});
process.on("exit", cleanup);
