import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  existsSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { DEFAULT_EXTENSION_ID, installChromeNativeHost } from "./install.mjs";

const EXTENSION_ID = "ikidefdlcddfomimpckflbajjccnmfna";
const HOST_SCRIPT = fileURLToPath(new URL("./native-host.mjs", import.meta.url));
const sha256File = (path) => createHash("sha256").update(readFileSync(path)).digest("hex");

test("installer creates private stable key and exact-origin macOS Chrome manifest", function (t) {
  const home = mkdtempSync(join(tmpdir(), "lab-host-install-"));
  t.after(function () {
    rmSync(home, { recursive: true, force: true });
  });

  assert.equal(DEFAULT_EXTENSION_ID, EXTENSION_ID);
  const first = installChromeNativeHost({
    browser: "chrome",
    homeDir: home,
    hostScriptPath: HOST_SCRIPT
  });
  assert.equal(first.key_created, true);
  assert.equal(statSync(first.key_path).mode & 0o777, 0o600);
  assert.equal(statSync(join(home, ".local-agent-bridge", "web-host")).mode & 0o777, 0o700);

  const before = readFileSync(first.key_path);
  const manifest = JSON.parse(readFileSync(first.manifest_path, "utf8"));
  assert.equal(manifest.path, HOST_SCRIPT);
  assert.deepEqual(manifest.allowed_origins, [
    "chrome-extension://" + EXTENSION_ID + "/"
  ]);
  assert.equal(JSON.stringify(manifest).includes("*"), false);

  const second = installChromeNativeHost({
    extensionId: EXTENSION_ID,
    browser: "chrome",
    homeDir: home,
    hostScriptPath: HOST_SCRIPT
  });
  assert.equal(second.key_created, false);
  assert.deepEqual(readFileSync(second.key_path), before);
});


test("installer writes NativeMessagingHosts under an explicit custom user-data-dir", function (t) {
  const home = mkdtempSync(join(tmpdir(), "lab-host-install-home-"));
  const profile = mkdtempSync(join(tmpdir(), "lab-host-install-profile-"));
  t.after(function () {
    rmSync(home, { recursive: true, force: true });
    rmSync(profile, { recursive: true, force: true });
  });

  const result = installChromeNativeHost({
    browser: "chrome-for-testing",
    homeDir: home,
    userDataDir: profile,
    hostScriptPath: HOST_SCRIPT
  });
  assert.equal(result.manifest_scope, "custom_user_data_dir");
  assert.equal(result.browser_user_data_dir, profile);
  assert.equal(
    result.manifest_path,
    join(profile, "NativeMessagingHosts", "com.local_agent_bridge.web_host.json")
  );
  const manifest = JSON.parse(readFileSync(result.manifest_path, "utf8"));
  assert.deepEqual(manifest.allowed_origins, [
    "chrome-extension://" + EXTENSION_ID + "/"
  ]);
  assert.equal(statSync(join(profile, "NativeMessagingHosts")).mode & 0o777, 0o700);
});

test("installer rejects wildcard-like malformed or manifest-mismatched extension ids", function () {
  assert.throws(function () {
    installChromeNativeHost({
      extensionId: "*",
      browser: "chrome",
      homeDir: tmpdir(),
      hostScriptPath: HOST_SCRIPT
    });
  }, /extension id/);

  assert.throws(function () {
    installChromeNativeHost({
      extensionId: "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
      browser: "chrome",
      homeDir: tmpdir(),
      hostScriptPath: HOST_SCRIPT
    });
  }, /extension id/);

  assert.throws(function () {
    installChromeNativeHost({
      extensionId: "abcdefghijklmnopabcdefghijklmnop",
      browser: "chrome",
      homeDir: tmpdir(),
      hostScriptPath: HOST_SCRIPT
    });
  }, /does not match the manifest public key/);
});


test("installer optionally registers a pinned complete Runtime verifier bundle", function (t) {
  const home = mkdtempSync(join(tmpdir(), "lab-host-runtime-verifier-"));
  t.after(function () { rmSync(home, { recursive: true, force: true }); });
  const result = installChromeNativeHost({
    browser: "chrome", homeDir: home, hostScriptPath: HOST_SCRIPT, registerRuntimeVerifier: true
  });
  const configPath = join(home, ".codex", "adaptive-delivery-host-attestation-verifiers.json");
  assert.equal(result.runtime_verifier_config_path, configPath);
  assert.equal(statSync(configPath).mode & 0o777, 0o600);
  assert.equal(statSync(join(home, ".codex")).mode & 0o777, 0o700);
  const config = JSON.parse(readFileSync(configPath, "utf8"));
  const record = config.verifiers.web;
  assert.equal(record.protocol, "runtime_host_verifier_cli_v1");
  assert.equal(record.executable.endsWith("/runtime-verifier-cli.mjs"), true);
  assert.equal(record.sha256, record.bundle_sha256[record.executable]);
  assert.equal(Object.keys(record.bundle_sha256).length, 21);
  assert.equal(record.bundle_sha256[process.execPath], sha256File(process.execPath));
  for (const digest of Object.values(record.bundle_sha256)) assert.match(digest, /^[0-9a-f]{64}$/);
  const pinnedPaths = Object.keys(record.bundle_sha256);
  for (const suffix of [
    "/extension-id.mjs",
    "/native-host.mjs",
    "/native-host-core.mjs",
    "/native-framing.mjs",
    "/extension/timeout-budget.js",
    "/extension/manifest.json",
    "/extension/background.js",
    "/extension/current-entry-registry.js",
    "/extension/debugger-session.js",
    "/extension/observation.js",
    "/extension/browser-submit.js",
    "/extension/submit-page.js",
    "/extension/submit-journal.js",
    "/extension/trusted-input.js",
    "/extension/submit-transaction-queue.js"
  ]) {
    assert.equal(pinnedPaths.some(function (path) { return path.endsWith(suffix); }), true, suffix);
  }
});

test("installer honors the cross-implementation Runtime verifier publication lock", function (t) {
  const home = mkdtempSync(join(tmpdir(), "lab-host-runtime-lock-"));
  t.after(function () { rmSync(home, { recursive: true, force: true }); });
  const configDir = join(home, ".codex");
  mkdirSync(configDir, { recursive: true, mode: 0o700 });
  const lockPath = join(configDir, "adaptive-delivery-host-attestation-verifiers.json.lock");
  writeFileSync(lockPath, "other-writer\n", { mode: 0o600 });
  assert.throws(function () {
    installChromeNativeHost({
      browser: "chrome",
      homeDir: home,
      hostScriptPath: HOST_SCRIPT,
      registerRuntimeVerifier: true
    });
  }, /publication lock is already held/);
  assert.equal(
    existsSync(join(home, "Library", "Application Support", "Google", "Chrome", "NativeMessagingHosts", "com.local_agent_bridge.web_host.json")),
    false
  );
});

test("installer rolls back the Native manifest when verifier registration fails", function (t) {
  const home = mkdtempSync(join(tmpdir(), "lab-host-runtime-rollback-"));
  t.after(function () { rmSync(home, { recursive: true, force: true }); });
  const configDir = join(home, ".codex");
  mkdirSync(configDir, { recursive: true, mode: 0o700 });
  const configPath = join(configDir, "adaptive-delivery-host-attestation-verifiers.json");
  const invalidConfig = "{\"schema_version\":2,\"verifiers\":{}}\n";
  writeFileSync(configPath, invalidConfig, { mode: 0o600 });
  assert.throws(function () {
    installChromeNativeHost({
      browser: "chrome",
      homeDir: home,
      hostScriptPath: HOST_SCRIPT,
      registerRuntimeVerifier: true
    });
  }, /existing Runtime Host verifier config is invalid/);
  const manifestPath = join(home, "Library", "Application Support", "Google", "Chrome", "NativeMessagingHosts", "com.local_agent_bridge.web_host.json");
  assert.equal(existsSync(manifestPath), false);
  assert.equal(readFileSync(configPath, "utf8"), invalidConfig);
});

test("standalone installer cannot overwrite a production-owned web verifier", function (t) {
  const home = mkdtempSync(join(tmpdir(), "lab-host-runtime-owner-"));
  t.after(function () { rmSync(home, { recursive: true, force: true }); });
  const configDir = join(home, ".codex");
  mkdirSync(configDir, { recursive: true, mode: 0o700 });
  const configPath = join(configDir, "adaptive-delivery-host-attestation-verifiers.json");
  const productionConfig = JSON.stringify({
    schema_version: 1,
    verifiers: {
      web: {
        protocol: "runtime_host_verifier_cli_v1",
        executable: "/private/production/runtime-verifier-production",
        sha256: "a".repeat(64),
        bundle_sha256: { "/private/production/runtime-verifier-production": "a".repeat(64) }
      }
    }
  }, null, 2) + "\n";
  writeFileSync(configPath, productionConfig, { mode: 0o600 });
  assert.throws(function () {
    installChromeNativeHost({
      browser: "chrome",
      homeDir: home,
      hostScriptPath: HOST_SCRIPT,
      registerRuntimeVerifier: true
    });
  }, /refusing to replace.*owned by another installer/);
  assert.equal(readFileSync(configPath, "utf8"), productionConfig);
});
