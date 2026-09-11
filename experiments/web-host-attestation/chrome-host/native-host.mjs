#!/usr/bin/env node
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import net from "node:net";
import {
  chmodSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync
} from "node:fs";
import { homedir } from "node:os";
import { dirname, isAbsolute, join, resolve } from "node:path";
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
const receiptStatePath = process.env.LAB_HOST_TOOL_RECEIPT_STATE_FILE || join(defaultRoot, "tool-receipts.json");
const bindingPath = String(process.env.LAB_HOST_EXTENSION_BINDING_FILE || "").trim();
const EXTENSION_ID_PATTERN = /^[a-p]{32}$/;
const EXTENSION_INSTANCE_ID_PATTERN = /^ei_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const EXTENSION_HELLO_ID_PATTERN = /^eh_([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/;
const EXTENSION_BUNDLE_RELATIVE_PATHS = Object.freeze([
  "timeout-budget.js", "manifest.json", "background.js", "current-entry-registry.js",
  "debugger-session.js", "observation.js", "browser-submit.js", "submit-page.js",
  "submit-journal.js", "trusted-input.js", "submit-transaction-queue.js"
]);
const nativeHostStartedAtMs = Date.now();

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

function readPrivateExtensionBinding(path) {
  if (!path) return null;
  const stat = lstatSync(path);
  if (!stat.isFile() || stat.isSymbolicLink()) {
    throw new Error("Host extension binding must be a regular non-symlink file");
  }
  const uid = currentUid();
  if (uid != null && stat.uid !== uid) throw new Error("Host extension binding owner mismatch");
  if ((stat.mode & 0o077) !== 0) throw new Error("Host extension binding permissions must be 0600 or stricter");
  const bytes = readFileSync(path);
  if (bytes.length < 2 || bytes.length > 64 * 1024) throw new Error("Host extension binding size is invalid");
  let binding;
  try {
    binding = JSON.parse(bytes.toString("utf8"));
  } catch {
    throw new Error("Host extension binding is not valid JSON");
  }
  if (!binding || typeof binding !== "object" || Array.isArray(binding) ||
      binding.schema_version !== "runtime_host_extension_binding_v1") {
    throw new Error("Host extension binding schema is invalid");
  }
  const extensionId = String(binding.extension_id || "").trim();
  if (!EXTENSION_ID_PATTERN.test(extensionId)) {
    throw new Error("Host extension binding extension_id is invalid");
  }
  const createdAtMs = Number(binding.created_at_unix_ms);
  if (!Number.isSafeInteger(createdAtMs) || createdAtMs < 1) {
    throw new Error("Host extension binding created_at_unix_ms is invalid");
  }
  const releaseRevision = String(binding.release_revision || "");
  const extensionDirRaw = String(binding.extension_dir || "");
  if (!/^[0-9a-f]{40}$/.test(releaseRevision) || !isAbsolute(extensionDirRaw)) {
    throw new Error("Host extension binding release identity is invalid");
  }
  const extensionDir = resolve(extensionDirRaw);
  const bundleRecords = binding.bundle_sha256;
  if (!bundleRecords || typeof bundleRecords !== "object" || Array.isArray(bundleRecords)) {
    throw new Error("Host extension binding bundle_sha256 is invalid");
  }
  const expectedBundlePaths = EXTENSION_BUNDLE_RELATIVE_PATHS.map((relative) =>
    resolve(join(extensionDir, relative))
  ).sort();
  if (Object.keys(bundleRecords).map((path) => resolve(path)).sort().join("\n") !==
      expectedBundlePaths.join("\n")) {
    throw new Error("Host extension binding bundle paths are invalid");
  }
  const bundleSha256 = new Map();
  for (const path of expectedBundlePaths) {
    const digest = String(bundleRecords[path] || "");
    if (!/^[0-9a-f]{64}$/.test(digest)) {
      throw new Error("Host extension binding bundle digest is invalid");
    }
    bundleSha256.set(path, digest);
  }
  const preferencePaths = binding.preference_paths;
  if (!Array.isArray(preferencePaths) || preferencePaths.length < 1 || preferencePaths.length > 32) {
    throw new Error("Host extension binding preference_paths is invalid");
  }
  const browserProfiles = new Set();
  const preferencePathSet = new Set();
  for (const rawPath of preferencePaths) {
    const preferencePath = String(rawPath || "");
    if (!isAbsolute(preferencePath) || dirname(preferencePath).split("/").pop() !== "Default") {
      throw new Error("Host extension binding preference path is invalid");
    }
    const resolvedPreferencePath = resolve(preferencePath);
    if (preferencePathSet.has(resolvedPreferencePath)) {
      throw new Error("Host extension binding preference path is duplicated");
    }
    preferencePathSet.add(resolvedPreferencePath);
    browserProfiles.add(resolve(dirname(dirname(resolvedPreferencePath))));
  }
  const browserExecutableRecords = binding.browser_executables;
  if (!Array.isArray(browserExecutableRecords) || browserExecutableRecords.length < 1 ||
      browserExecutableRecords.length > 8) {
    throw new Error("Host extension binding browser_executables is invalid");
  }
  const browserExecutables = new Map();
  for (const record of browserExecutableRecords) {
    if (!record || typeof record !== "object" || Array.isArray(record) ||
        Object.keys(record).sort().join(",") !== "path,sha256") {
      throw new Error("Host extension binding browser executable pin is invalid");
    }
    const executablePath = String(record.path || "");
    const digest = String(record.sha256 || "");
    if (!isAbsolute(executablePath) ||
        !(executablePath.endsWith(".app/Contents/MacOS/Google Chrome") ||
          executablePath.endsWith(".app/Contents/MacOS/Google Chrome for Testing")) ||
        !/^[0-9a-f]{64}$/.test(digest) ||
        browserExecutables.has(resolve(executablePath))) {
      throw new Error("Host extension binding browser executable pin is invalid");
    }
    browserExecutables.set(resolve(executablePath), digest);
  }
  return Object.freeze({
    extension_id: extensionId,
    created_at_ms: createdAtMs,
    release_revision: releaseRevision,
    extension_dir: extensionDir,
    bundle_sha256: bundleSha256,
    binding_sha256: createHash("sha256").update(bytes).digest("hex"),
    browser_profiles: Object.freeze(Array.from(browserProfiles).sort()),
    preference_paths: Object.freeze(Array.from(preferencePathSet).sort()),
    browser_executables: browserExecutables
  });
}

function validateBoundFile(path, expectedDigest, label, { privateFile = false } = {}) {
  let info;
  let bytes;
  try {
    info = lstatSync(path);
    if (!info.isFile() || info.isSymbolicLink()) throw new Error("unsafe file");
    const uid = currentUid();
    if ((uid != null && info.uid !== uid) ||
        (privateFile ? (info.mode & 0o077) !== 0 : (info.mode & 0o022) !== 0)) {
      throw new Error("unsafe file");
    }
    bytes = readFileSync(path);
  } catch {
    throw new Error(label + " is unavailable or unsafe");
  }
  if (createHash("sha256").update(bytes).digest("hex") !== expectedDigest) {
    throw new Error(label + " hash mismatch");
  }
}

function validateCurrentProductionBinding(startupBinding) {
  const currentBinding = readPrivateExtensionBinding(bindingPath);
  if (!currentBinding || currentBinding.binding_sha256 !== startupBinding.binding_sha256) {
    throw new Error("Host extension binding changed after startup");
  }
  for (const [path, digest] of currentBinding.bundle_sha256) {
    validateBoundFile(path, digest, "Host extension bundle member");
  }
  if (!detectBoundBrowser(process.ppid, currentBinding)) {
    throw new Error("Native Host browser identity changed after startup");
  }
  return currentBinding;
}

function psField(parentPid, field) {
  const completed = spawnSync("/bin/ps", ["-ww", "-p", String(parentPid), "-o", field + "="], {
    encoding: "utf8",
    timeout: 1000,
    maxBuffer: 64 * 1024
  });
  if (completed.status !== 0 || typeof completed.stdout !== "string") return null;
  const value = completed.stdout.trim();
  return value || null;
}

function commandSwitchValue(argumentsText, name) {
  const escapedName = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const marker = new RegExp("(?:^|\\s)--" + escapedName + "(?:=|\\s)", "g");
  if ((argumentsText.match(marker) || []).length !== 1) return null;
  const value = new RegExp("(?:^|\\s)--" + escapedName + "=([^\\s\\\"']+)(?=\\s|$)")
    .exec(argumentsText);
  return value ? value[1] : null;
}

function readPrivateChromePreferences(preferencePath, label) {
  try {
    const info = lstatSync(preferencePath);
    if (!info.isFile() || info.isSymbolicLink()) throw new Error("unsafe profile preferences");
    const uid = currentUid();
    if ((uid != null && info.uid !== uid) || (info.mode & 0o077) !== 0 ||
        info.size < 2 || info.size > 16 * 1024 * 1024) {
      throw new Error("unsafe profile preferences");
    }
    return JSON.parse(readFileSync(preferencePath, "utf8"));
  } catch {
    throw new Error(label);
  }
}

function assertNoUnboundProfileHasExtension(extensionBinding) {
  const boundPreferences = new Set(extensionBinding.preference_paths);
  for (const preferencePath of boundPreferences) {
    const value = readPrivateChromePreferences(
      preferencePath, "Native Host cannot validate a bound Chrome profile"
    );
    const record = value?.extensions?.settings?.[extensionBinding.extension_id];
    const configuredPath = record && typeof record === "object" ? record.path : null;
    if (!isAbsolute(String(configuredPath || "")) ||
        resolve(String(configuredPath)) !== extensionBinding.extension_dir) {
      throw new Error("Native Host extension path does not match the bound Chrome profile");
    }
  }
  for (const userDataDir of extensionBinding.browser_profiles) {
    let entries;
    try {
      entries = readdirSync(userDataDir, { withFileTypes: true });
    } catch {
      throw new Error("Native Host cannot inspect managed Chrome profiles");
    }
    for (const entry of entries) {
      const preferencePath = resolve(join(userDataDir, entry.name, "Secure Preferences"));
      if (!entry.isDirectory()) {
        if (entry.isSymbolicLink() && existsSync(preferencePath)) {
          throw new Error("Native Host found an unsafe Chrome profile directory");
        }
        continue;
      }
      if (boundPreferences.has(preferencePath) || !existsSync(preferencePath)) continue;
      const value = readPrivateChromePreferences(
        preferencePath, "Native Host cannot validate an unbound Chrome profile"
      );
      const record = value?.extensions?.settings?.[extensionBinding.extension_id];
      if (record && typeof record === "object") {
        throw new Error("Native Host extension is present in an unbound Chrome profile");
      }
    }
  }
}

function detectBoundBrowser(parentPid, extensionBinding) {
  const pid = Number(parentPid);
  if (!Number.isSafeInteger(pid) || pid <= 1) return null;
  const executablePath = psField(pid, "comm");
  const command = psField(pid, "command");
  if (!executablePath || !command ||
      (command !== executablePath && !command.startsWith(executablePath + " "))) return null;
  const expectedDigest = extensionBinding.browser_executables.get(resolve(executablePath));
  if (!expectedDigest) return null;
  let actualDigest;
  try {
    actualDigest = createHash("sha256").update(readFileSync(executablePath)).digest("hex");
  } catch {
    return null;
  }
  if (actualDigest !== expectedDigest) return null;
  const argumentsText = command.slice(executablePath.length);
  const userDataDir = commandSwitchValue(argumentsText, "user-data-dir");
  const profileDirectory = commandSwitchValue(argumentsText, "profile-directory");
  if (!userDataDir || !profileDirectory || !/^[A-Za-z0-9 _.-]+$/.test(profileDirectory) ||
      profileDirectory === "." || profileDirectory === "..") return null;
  const browserProfile = resolve(userDataDir);
  if (!extensionBinding.browser_profiles.includes(browserProfile)) return null;
  const preferencePath = resolve(join(browserProfile, profileDirectory, "Secure Preferences"));
  if (!extensionBinding.preference_paths.includes(preferencePath)) return null;
  assertNoUnboundProfileHasExtension(extensionBinding);
  return Object.freeze({
    executable_path: resolve(executablePath),
    browser_profile: browserProfile,
    preference_path: preferencePath
  });
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
const extensionBinding = readPrivateExtensionBinding(bindingPath);
if (extensionBinding) {
  const expectedOrigin = `chrome-extension://${extensionBinding.extension_id}/`;
  if (process.argv[2] !== expectedOrigin || !detectBoundBrowser(process.ppid, extensionBinding)) {
    throw new Error("Native Host browser profile does not match extension binding");
  }
  validateCurrentProductionBinding(extensionBinding);
}
prepareSocket(socketPath);

let nativeInput = Buffer.alloc(0);
const pendingBrowser = new Map();
let readyExtensionEpoch = null;

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

function acceptExtensionHello(message) {
  if (extensionBinding) validateCurrentProductionBinding(extensionBinding);
  if (message.interface_version !== 1) throw new Error("extension hello interface_version is invalid");
  const extensionId = String(message.extension_id || "");
  if (!EXTENSION_ID_PATTERN.test(extensionId)) {
    throw new Error("extension hello extension_id is invalid");
  }
  if (extensionBinding && extensionId !== extensionBinding.extension_id) {
    throw new Error("extension hello extension_id does not match binding");
  }
  const helloId = String(message.request_id || "");
  const legacyHelloMatch = EXTENSION_HELLO_ID_PATTERN.exec(helloId);
  const instanceId = String(
    message.extension_instance_id ||
    (legacyHelloMatch ? "ei_" + legacyHelloMatch[1] : "")
  );
  if (!EXTENSION_INSTANCE_ID_PATTERN.test(instanceId)) {
    throw new Error("extension hello extension_instance_id is invalid");
  }
  const loadedAtMs = message.loaded_at_ms == null
    ? nativeHostStartedAtMs
    : Number(message.loaded_at_ms);
  if (!Number.isSafeInteger(loadedAtMs) || loadedAtMs < 1 ||
      (extensionBinding && loadedAtMs < extensionBinding.created_at_ms)) {
    throw new Error("extension hello loaded_at_ms is stale before binding creation");
  }
  const bindingSha256 = extensionBinding
    ? extensionBinding.binding_sha256
    : createHash("sha256").update("unbound-extension:" + extensionId).digest("hex");
  readyExtensionEpoch = Object.freeze({
    instance_id: instanceId,
    loaded_at_ms: loadedAtMs,
    binding_sha256: bindingSha256,
    instance_id_sha256: createHash("sha256").update(instanceId).digest("hex")
  });
}

function requireExtensionReady() {
  if (!readyExtensionEpoch) {
    throw new Error("HOST_EXTENSION_NOT_READY: valid extension hello required");
  }
  if (extensionBinding) validateCurrentProductionBinding(extensionBinding);
  return readyExtensionEpoch;
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
      try {
        acceptExtensionHello(message);
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
      } catch (error) {
        sendNative({
          kind: "extension_hello_ack",
          request_id: requestId,
          ok: false,
          error: String(error && error.message || error || "extension hello rejected")
        });
      }
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
  receiptStatePath: receiptStatePath,
  sendBrowserCommand: sendBrowserCommand,
  getLiveValidation: function () { return liveValidationState; },
  recordLiveValidation: function (record) {
    liveValidationState = Object.freeze({ ...record });
  },
  requireExtensionReady: requireExtensionReady
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
