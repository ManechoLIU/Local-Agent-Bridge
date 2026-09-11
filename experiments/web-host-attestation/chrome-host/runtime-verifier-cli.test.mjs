import test from "node:test";
import assert from "node:assert/strict";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { spawn } from "node:child_process";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { extensionIdFromManifestKey } from "./extension-id.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const CLI_CWD = dirname(HERE);
const CLI = "./chrome-host/runtime-verifier-cli.mjs";
const MANIFEST_KEY = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEApgCDknfvQq6AgxqJjx7YMJaAPyvoCt/fdoqtGO7ziTiIZUgNzUIf4iLN+a4y5dOnd8XkmUshvcbR9CPuYLW/kNJShuWyA850JxeMeDtJvU4StGjCfBJXs0R7AKdl98e5/Tqo5OdWcGTkjwkKxvW3rveM2lzLZuL77TwZE4tG5t/+YWqaEkfkALz1vjN2grrBkY7q9lCMzHfpWjOTycV1LwJ2rX/pTWV47+VB030iXxL5hhde2zXNuxYnwWX5m8y7lZQH7/r/wre1Fkf+FBxOix4Or+fy4Nm/x+13EoIpayl7+bZyijEEOP/gPtVycElAOB8u679GcQBFV+2+v0azWQIDAQAB";

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function writePrivateJson(path, value) {
  writeFileSync(path, JSON.stringify(value), { mode: 0o600 });
  chmodSync(path, 0o600);
}

function createBindingFixture(t) {
  const root = mkdtempSync(join(tmpdir(), "lab-runtime-verifier-binding-"));
  const extensionDir = join(root, "extension");
  const preferencePath = join(root, "Preferences");
  const bindingPath = join(root, "binding.json");
  mkdirSync(extensionDir, { mode: 0o700 });
  const manifestPath = join(extensionDir, "manifest.json");
  writePrivateJson(manifestPath, { manifest_version: 3, name: "test", version: "1.0", key: MANIFEST_KEY });
  const extensionId = extensionIdFromManifestKey(MANIFEST_KEY);
  writePrivateJson(preferencePath, {
    extensions: { settings: { [extensionId]: { path: extensionDir } } }
  });
  const binding = {
    schema_version: "runtime_host_extension_binding_v1",
    extension_id: extensionId,
    release_revision: "test-release",
    created_at_unix_ms: 1800000000000,
    extension_dir: extensionDir,
    preference_paths: [preferencePath],
    bundle_sha256: { [manifestPath]: sha256(manifestPath) }
  };
  writePrivateJson(bindingPath, binding);
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return { binding, bindingPath, extensionDir, manifestPath, preferencePath };
}

async function invoke(bindingPath) {
  const child = spawn(process.execPath, [CLI], {
    cwd: CLI_CWD,
    env: { ...process.env, LAB_HOST_EXTENSION_BINDING_FILE: bindingPath },
    stdio: ["pipe", "pipe", "pipe"]
  });
  let stdout = "";
  child.stdout.on("data", (chunk) => { stdout += chunk.toString("utf8"); });
  child.stdin.end(JSON.stringify({ operation: "unsupported_operation" }) + "\n");
  const [code] = await once(child, "exit");
  return { code, payload: JSON.parse(stdout.trim()) };
}

test("Runtime verifier CLI permits an operation only after a valid extension binding", async function (t) {
  const { bindingPath } = createBindingFixture(t);
  const result = await invoke(bindingPath);
  assert.equal(result.code, 1);
  assert.match(result.payload.error, /unsupported Runtime Host operation/);
});

test("Runtime verifier CLI permits read-only public extension bundle members", async function (t) {
  const { bindingPath, manifestPath } = createBindingFixture(t);
  chmodSync(manifestPath, 0o644);
  const result = await invoke(bindingPath);
  assert.equal(result.code, 1);
  assert.match(result.payload.error, /unsupported Runtime Host operation/);
});

test("Runtime verifier CLI rejects extension bundle members writable by the group", async function (t) {
  const { bindingPath, manifestPath } = createBindingFixture(t);
  chmodSync(manifestPath, 0o664);
  const result = await invoke(bindingPath);
  assert.equal(result.code, 1);
  assert.match(result.payload.error, /bundle member.*writable/i);
});

test("Runtime verifier CLI rejects preference paths that drift from the bound extension", async function (t) {
  const { binding, bindingPath, preferencePath } = createBindingFixture(t);
  writePrivateJson(preferencePath, { extensions: { settings: { [binding.extension_id]: { path: join(binding.extension_dir, "other") } } } });
  const result = await invoke(bindingPath);
  assert.equal(result.code, 1);
  assert.match(result.payload.error, /preference.*extension.*path/i);
});

test("Runtime verifier CLI rejects a changed bundle member", async function (t) {
  const { bindingPath, manifestPath } = createBindingFixture(t);
  writePrivateJson(manifestPath, { manifest_version: 3, name: "changed", version: "1.0", key: MANIFEST_KEY });
  const result = await invoke(bindingPath);
  assert.equal(result.code, 1);
  assert.match(result.payload.error, /bundle.*hash/i);
});

test("Runtime verifier CLI rejects symlinked and non-private binding files", async function (t) {
  const { bindingPath } = createBindingFixture(t);
  chmodSync(bindingPath, 0o644);
  let result = await invoke(bindingPath);
  assert.equal(result.code, 1);
  assert.match(result.payload.error, /binding.*permissions/i);

  chmodSync(bindingPath, 0o600);
  const linkPath = bindingPath + ".link";
  symlinkSync(bindingPath, linkPath);
  result = await invoke(linkPath);
  assert.equal(result.code, 1);
  assert.match(result.payload.error, /binding.*non-symlink/i);
});
