import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { once } from "node:events";
import {
  chmodSync,
  copyFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
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
const EXTENSION_FILES = [
  "timeout-budget.js", "manifest.json", "background.js", "current-entry-registry.js",
  "debugger-session.js", "observation.js", "browser-submit.js", "submit-page.js",
  "submit-journal.js", "trusted-input.js", "submit-transaction-queue.js"
];

function sha256File(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function writeProfilePreferences(
  userDataDir,
  profileName = "Default",
  includeExtension = true,
  extensionPath = null
) {
  const profileDir = join(userDataDir, profileName);
  const path = join(profileDir, "Secure Preferences");
  mkdirSync(profileDir, { recursive: true });
  const settings = includeExtension
    ? { ikidefdlcddfomimpckflbajjccnmfna: { state: 1, path: extensionPath } }
    : {};
  writeFileSync(path, JSON.stringify({ extensions: { settings: settings } }), { mode: 0o600 });
  chmodSync(path, 0o600);
  return path;
}

function bindingFixture(root, preferencePaths, browserExecutable, createdAtMs) {
  const extensionDir = join(root, "extension");
  mkdirSync(extensionDir, { recursive: true });
  const bundle = {};
  for (const relative of EXTENSION_FILES) {
    const path = join(extensionDir, relative);
    writeFileSync(path, "fixture:" + relative + "\n", { mode: 0o600 });
    chmodSync(path, 0o600);
    bundle[path] = sha256File(path);
  }
  return {
    value: {
      schema_version: "runtime_host_extension_binding_v1",
      extension_id: "ikidefdlcddfomimpckflbajjccnmfna",
      release_revision: "a".repeat(40),
      created_at_unix_ms: createdAtMs,
      extension_dir: extensionDir,
      preference_paths: preferencePaths,
      browser_executables: [{ path: browserExecutable, sha256: sha256File(browserExecutable) }],
      bundle_sha256: bundle
    },
    bundle_member: join(extensionDir, "background.js")
  };
}

function waitForSocket(path) {
  return new Promise(function (resolve, reject) {
    let remaining = 200;
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
  const bindingPath = join(root, "extension-binding.json");
  const userDataDir = join(root, "chrome-profile");
  const launcherPath = join(root, "browser-launcher.mjs");
  const browserExecutable = join(
    root, "Google Chrome for Testing.app", "Contents", "MacOS", "Google Chrome for Testing"
  );
  const bindingCreatedAtMs = Date.now();
  mkdirSync(join(root, "Google Chrome for Testing.app", "Contents", "MacOS"), { recursive: true });
  copyFileSync(process.execPath, browserExecutable);
  chmodSync(browserExecutable, 0o700);
  writeFileSync(keyPath, SECRET, { mode: 0o600 });
  chmodSync(keyPath, 0o600);
  const binding = bindingFixture(
    root,
    [join(userDataDir, "Default", "Secure Preferences")],
    browserExecutable,
    bindingCreatedAtMs
  );
  writeProfilePreferences(userDataDir, "Default", true, binding.value.extension_dir);
  writeFileSync(bindingPath, JSON.stringify(binding.value), { mode: 0o600 });
  chmodSync(bindingPath, 0o600);
  writeFileSync(launcherPath, [
    'import { spawn } from "node:child_process";',
    'const child = spawn(process.execPath, [process.env.LAB_NATIVE_HOST_SCRIPT, process.env.LAB_EXTENSION_ORIGIN], { env: process.env, stdio: ["inherit", "inherit", "inherit"] });',
    'child.on("exit", (code) => process.exit(code == null ? 1 : code));',
    'process.on("SIGTERM", () => child.kill("SIGTERM"));',
    ''
  ].join("\n"), { mode: 0o600 });

  const child = spawn(browserExecutable, [
    launcherPath,
    "--user-data-dir=" + userDataDir,
    "--profile-directory=Default"
  ], {
    cwd: new URL("..", import.meta.url).pathname,
    env: Object.assign({}, process.env, {
      LAB_NATIVE_HOST_SCRIPT: fileURLToPath(new URL("./native-host.mjs", import.meta.url)),
      LAB_EXTENSION_ORIGIN: "chrome-extension://ikidefdlcddfomimpckflbajjccnmfna/",
      LAB_HOST_ATTESTATION_KEY_FILE: keyPath,
      LAB_HOST_ADAPTER_SOCKET: socketPath,
      LAB_HOST_EXTENSION_BINDING_FILE: bindingPath
    }),
    stdio: ["pipe", "pipe", "pipe"]
  });

  t.after(function () {
    try { child.kill("SIGTERM"); } catch {}
    rmSync(root, { recursive: true, force: true });
  });

  let stdoutBuffer = Buffer.alloc(0);
  let stderr = "";
  const pendingHelloAcks = new Map();
  function nextHelloAck(requestId) {
    return new Promise(function (resolve, reject) {
      const timer = setTimeout(function () {
        pendingHelloAcks.delete(requestId);
        reject(new Error("extension hello acknowledgement timed out"));
      }, 2000);
      pendingHelloAcks.set(requestId, function (message) {
        clearTimeout(timer);
        resolve(message);
      });
    });
  }
  child.stderr.on("data", function (chunk) {
    stderr += chunk.toString("utf8");
  });

  child.stdout.on("data", function (chunk) {
    stdoutBuffer = Buffer.concat([stdoutBuffer, chunk]);
    const parsed = consumeNativeFrames(stdoutBuffer);
    stdoutBuffer = parsed.remainder;
    for (const command of parsed.messages) {
      if (command.kind === "extension_hello_ack") {
        const resolve = pendingHelloAcks.get(command.request_id);
        pendingHelloAcks.delete(command.request_id);
        if (resolve) resolve(command);
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

  await assert.rejects(
    requestNativeHost(socketPath, { op: "health" }, { timeoutMs: 3000 }),
    /extension.*ready|extension.*hello/i
  );

  const staleHelloAck = nextHelloAck("eh_stale");
  child.stdin.write(encodeNativeMessage({
    request_id: "eh_stale",
    op: "extension_hello",
    interface_version: 1,
    extension_id: "ikidefdlcddfomimpckflbajjccnmfna",
    extension_instance_id: "ei_00000000-0000-4000-8000-000000000001",
    loaded_at_ms: bindingCreatedAtMs - 1
  }));
  const staleHello = await staleHelloAck;
  assert.equal(staleHello.ok, false);
  assert.match(staleHello.error, /loaded.*before.*binding|stale/i);
  await assert.rejects(
    requestNativeHost(socketPath, { op: "health" }, { timeoutMs: 3000 }),
    /extension.*ready|extension.*hello/i
  );

  const currentHelloId = "eh_00000000-0000-4000-8000-000000000002";
  const helloAck = nextHelloAck(currentHelloId);
  child.stdin.write(encodeNativeMessage({
    request_id: currentHelloId,
    op: "extension_hello",
    interface_version: 1,
    extension_id: "ikidefdlcddfomimpckflbajjccnmfna"
  }));
  const hello = await helloAck;
  assert.deepEqual(hello, {
    kind: "extension_hello_ack",
    request_id: currentHelloId,
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
  assert.equal(health.extension_ready, true);
  assert.match(health.extension_instance_id_sha256, /^[0-9a-f]{64}$/);
  assert.equal(health.extension_loaded_at_ms >= bindingCreatedAtMs, true);
  assert.match(health.extension_binding_sha256, /^[0-9a-f]{64}$/);
  assert.equal(JSON.stringify(health).includes(bindingPath), false);
  assert.equal(stderr, "");

  writeFileSync(binding.bundle_member, "tampered extension bundle\n", { mode: 0o600 });
  await assert.rejects(
    requestNativeHost(socketPath, { op: "health" }, { timeoutMs: 3000 }),
    /extension bundle member hash mismatch/i
  );
  writeFileSync(binding.bundle_member, "fixture:background.js\n", { mode: 0o600 });
  assert.equal((await requestNativeHost(socketPath, { op: "health" }, { timeoutMs: 3000 })).status, "ready");

  const browserBytes = readFileSync(browserExecutable);
  writeFileSync(browserExecutable, Buffer.concat([browserBytes, Buffer.from("drift")]), { mode: 0o700 });
  await assert.rejects(
    requestNativeHost(socketPath, { op: "health" }, { timeoutMs: 3000 }),
    /browser identity changed/i
  );
  writeFileSync(browserExecutable, browserBytes, { mode: 0o700 });
  assert.equal((await requestNativeHost(socketPath, { op: "health" }, { timeoutMs: 3000 })).status, "ready");

  const bindingBytes = readFileSync(bindingPath);
  writeFileSync(bindingPath, Buffer.concat([bindingBytes, Buffer.from("\n")]), { mode: 0o600 });
  await assert.rejects(
    requestNativeHost(socketPath, { op: "health" }, { timeoutMs: 3000 }),
    /binding changed after startup/i
  );
  writeFileSync(bindingPath, bindingBytes, { mode: 0o600 });
  assert.equal((await requestNativeHost(socketPath, { op: "health" }, { timeoutMs: 3000 })).status, "ready");

  writeProfilePreferences(userDataDir, "Default", true, join(root, "different-extension"));
  await assert.rejects(
    requestNativeHost(socketPath, { op: "health" }, { timeoutMs: 3000 }),
    /extension path does not match/i
  );
  writeProfilePreferences(userDataDir, "Default", true, binding.value.extension_dir);
  assert.equal((await requestNativeHost(socketPath, { op: "health" }, { timeoutMs: 3000 })).status, "ready");

  child.stdin.end();
  const exit = await once(child, "exit");
  assert.equal(exit[0], 0);
  assert.equal(existsSync(socketPath), false);
});

test("native Host rejects unmanaged roots, untrusted parents, and duplicate profile installs", async function (t) {
  const root = mkdtempSync(join(tmpdir(), "lab-native-host-profile-"));
  const keyPath = join(root, "attestation.key");
  const socketPath = join(root, "host.sock");
  const bindingPath = join(root, "extension-binding.json");
  const managedUserDataDir = join(root, "managed-profile");
  const unmanagedUserDataDir = join(root, "unmanaged-profile");
  const launcherPath = join(root, "browser-launcher.mjs");
  const pinnedBrowserExecutable = join(
    root, "Google Chrome.app", "Contents", "MacOS", "Google Chrome"
  );
  mkdirSync(join(root, "Google Chrome.app", "Contents", "MacOS"), { recursive: true });
  copyFileSync(process.execPath, pinnedBrowserExecutable);
  chmodSync(pinnedBrowserExecutable, 0o700);
  writeFileSync(keyPath, SECRET, { mode: 0o600 });
  chmodSync(keyPath, 0o600);
  const binding = bindingFixture(
    root,
    [join(managedUserDataDir, "Default", "Secure Preferences")],
    pinnedBrowserExecutable,
    Date.now()
  );
  writeProfilePreferences(
    managedUserDataDir, "Default", true, binding.value.extension_dir
  );
  writeProfilePreferences(unmanagedUserDataDir, "Default", true, join(root, "other-extension"));
  writeFileSync(bindingPath, JSON.stringify(binding.value), { mode: 0o600 });
  chmodSync(bindingPath, 0o600);
  writeFileSync(launcherPath, [
    'import { spawn } from "node:child_process";',
    'const child = spawn(process.execPath, [process.env.LAB_NATIVE_HOST_SCRIPT, process.env.LAB_EXTENSION_ORIGIN], { env: process.env, stdio: ["inherit", "inherit", "inherit"] });',
    'child.on("exit", (code) => process.exit(code == null ? 1 : code));',
    ''
  ].join("\n"), { mode: 0o600 });

  const child = spawn(pinnedBrowserExecutable, [
    launcherPath,
    "--user-data-dir=" + unmanagedUserDataDir,
    "--profile-directory=Default"
  ], {
    env: Object.assign({}, process.env, {
      LAB_NATIVE_HOST_SCRIPT: fileURLToPath(new URL("./native-host.mjs", import.meta.url)),
      LAB_EXTENSION_ORIGIN: "chrome-extension://ikidefdlcddfomimpckflbajjccnmfna/",
      LAB_HOST_ATTESTATION_KEY_FILE: keyPath,
      LAB_HOST_ADAPTER_SOCKET: socketPath,
      LAB_HOST_EXTENSION_BINDING_FILE: bindingPath
    }),
    stdio: ["pipe", "pipe", "pipe"]
  });
  t.after(function () {
    try { child.kill("SIGTERM"); } catch {}
    rmSync(root, { recursive: true, force: true });
  });
  let stderr = "";
  child.stderr.on("data", function (chunk) { stderr += chunk.toString("utf8"); });
  const exit = await once(child, "exit");
  assert.notEqual(exit[0], 0);
  assert.match(stderr, /browser profile does not match extension binding/i);
  assert.equal(existsSync(socketPath), false);

  const untrustedParent = spawn(
    process.execPath,
    [launcherPath, "--user-data-dir=" + managedUserDataDir, "--profile-directory=Default"],
    {
      env: Object.assign({}, process.env, {
        LAB_NATIVE_HOST_SCRIPT: fileURLToPath(new URL("./native-host.mjs", import.meta.url)),
        LAB_EXTENSION_ORIGIN: "chrome-extension://ikidefdlcddfomimpckflbajjccnmfna/",
        LAB_HOST_ATTESTATION_KEY_FILE: keyPath,
        LAB_HOST_ADAPTER_SOCKET: socketPath,
        LAB_HOST_EXTENSION_BINDING_FILE: bindingPath
      }),
      stdio: ["pipe", "pipe", "pipe"]
    }
  );
  let untrustedStderr = "";
  untrustedParent.stderr.on("data", function (chunk) {
    untrustedStderr += chunk.toString("utf8");
  });
  const untrustedExit = await once(untrustedParent, "exit");
  assert.notEqual(untrustedExit[0], 0);
  assert.match(untrustedStderr, /browser profile does not match extension binding/i);
  assert.equal(existsSync(socketPath), false);

  writeProfilePreferences(managedUserDataDir, "Profile 1", true, join(root, "malicious-extension"));
  const duplicateProfile = spawn(
    pinnedBrowserExecutable,
    [launcherPath, "--user-data-dir=" + managedUserDataDir, "--profile-directory=Default"],
    {
      env: Object.assign({}, process.env, {
        LAB_NATIVE_HOST_SCRIPT: fileURLToPath(new URL("./native-host.mjs", import.meta.url)),
        LAB_EXTENSION_ORIGIN: "chrome-extension://ikidefdlcddfomimpckflbajjccnmfna/",
        LAB_HOST_ATTESTATION_KEY_FILE: keyPath,
        LAB_HOST_ADAPTER_SOCKET: socketPath,
        LAB_HOST_EXTENSION_BINDING_FILE: bindingPath
      }),
      stdio: ["pipe", "pipe", "pipe"]
    }
  );
  let duplicateStderr = "";
  duplicateProfile.stderr.on("data", function (chunk) {
    duplicateStderr += chunk.toString("utf8");
  });
  const duplicateExit = await once(duplicateProfile, "exit");
  assert.notEqual(duplicateExit[0], 0);
  assert.match(duplicateStderr, /unbound Chrome profile/i);
  assert.equal(existsSync(socketPath), false);
});

test("native Host rejects conflicting duplicate browser profile switches", async function (t) {
  const root = mkdtempSync(join(tmpdir(), "lab-native-host-duplicate-profile-"));
  const keyPath = join(root, "attestation.key");
  const socketPath = join(root, "host.sock");
  const bindingPath = join(root, "extension-binding.json");
  const managedUserDataDir = join(root, "managed-profile");
  const launcherPath = join(root, "browser-launcher.mjs");
  const browserExecutable = join(
    root, "Google Chrome.app", "Contents", "MacOS", "Google Chrome"
  );
  mkdirSync(join(root, "Google Chrome.app", "Contents", "MacOS"), { recursive: true });
  copyFileSync(process.execPath, browserExecutable);
  chmodSync(browserExecutable, 0o700);
  writeFileSync(keyPath, SECRET, { mode: 0o600 });
  chmodSync(keyPath, 0o600);
  const binding = bindingFixture(
    root,
    [join(managedUserDataDir, "Default", "Secure Preferences")],
    browserExecutable,
    Date.now()
  );
  writeProfilePreferences(
    managedUserDataDir, "Default", true, binding.value.extension_dir
  );
  writeFileSync(bindingPath, JSON.stringify(binding.value), { mode: 0o600 });
  chmodSync(bindingPath, 0o600);
  writeFileSync(launcherPath, [
    'import { spawn } from "node:child_process";',
    'const child = spawn(process.execPath, [process.env.LAB_NATIVE_HOST_SCRIPT, process.env.LAB_EXTENSION_ORIGIN], { env: process.env, stdio: ["inherit", "inherit", "inherit"] });',
    'child.on("exit", (code) => process.exit(code == null ? 1 : code));',
    ''
  ].join("\n"), { mode: 0o600 });
  const child = spawn(browserExecutable, [
    launcherPath,
    "--user-data-dir=" + managedUserDataDir,
    "--user-data-dir=" + join(root, "other-profile"),
    "--profile-directory=Default"
  ], {
    env: Object.assign({}, process.env, {
      LAB_NATIVE_HOST_SCRIPT: fileURLToPath(new URL("./native-host.mjs", import.meta.url)),
      LAB_EXTENSION_ORIGIN: "chrome-extension://ikidefdlcddfomimpckflbajjccnmfna/",
      LAB_HOST_ATTESTATION_KEY_FILE: keyPath,
      LAB_HOST_ADAPTER_SOCKET: socketPath,
      LAB_HOST_EXTENSION_BINDING_FILE: bindingPath
    }),
    stdio: ["pipe", "pipe", "pipe"]
  });
  t.after(function () {
    try { child.kill("SIGTERM"); } catch {}
    rmSync(root, { recursive: true, force: true });
  });
  let stderr = "";
  child.stderr.on("data", function (chunk) { stderr += chunk.toString("utf8"); });
  const exit = await once(child, "exit");
  assert.notEqual(exit[0], 0);
  assert.match(stderr, /browser profile does not match extension binding/i);
  assert.equal(existsSync(socketPath), false);
});
