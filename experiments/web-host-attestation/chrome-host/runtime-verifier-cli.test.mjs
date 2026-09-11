import test from "node:test";
import assert from "node:assert/strict";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { spawn } from "node:child_process";
import net from "node:net";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { extensionIdFromManifestKey } from "./extension-id.mjs";
import { ToolInvocationReceiptJournal, signCurrentEntryEvidence } from "../attestation.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const CLI_CWD = dirname(HERE);
const CLI = "./chrome-host/runtime-verifier-cli.mjs";
const MANIFEST_KEY = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEApgCDknfvQq6AgxqJjx7YMJaAPyvoCt/fdoqtGO7ziTiIZUgNzUIf4iLN+a4y5dOnd8XkmUshvcbR9CPuYLW/kNJShuWyA850JxeMeDtJvU4StGjCfBJXs0R7AKdl98e5/Tqo5OdWcGTkjwkKxvW3rveM2lzLZuL77TwZE4tG5t/+YWqaEkfkALz1vjN2grrBkY7q9lCMzHfpWjOTycV1LwJ2rX/pTWV47+VB030iXxL5hhde2zXNuxYnwWX5m8y7lZQH7/r/wre1Fkf+FBxOix4Or+fy4Nm/x+13EoIpayl7+bZyijEEOP/gPtVycElAOB8u679GcQBFV+2+v0azWQIDAQAB";

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function receiptSha256(receipt) {
  return createHash("sha256").update(canonicalJson(receipt)).digest("hex");
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

async function invokeV2WithCallerIdentity(t, field) {
  const root = mkdtempSync(join(tmpdir(), "lab-runtime-verifier-v2-"));
  const keyPath = join(root, "attestation.key");
  writeFileSync(keyPath, "v2-cli-test-secret-0123456789abcdef", { mode: 0o600 });
  chmodSync(keyPath, 0o600);
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const child = spawn(process.execPath, [CLI], {
    cwd: CLI_CWD,
    env: {
      ...process.env,
      LAB_HOST_ATTESTATION_KEY_FILE: keyPath,
      LAB_HOST_ADAPTER_SOCKET: join(root, "unused.sock")
    },
    stdio: ["pipe", "pipe", "pipe"]
  });
  let stdout = "";
  child.stdout.on("data", (chunk) => { stdout += chunk.toString("utf8"); });
  child.stdin.end(JSON.stringify({
    protocol: "runtime_host_verifier_cli_v2",
    operation: "verify_tool_pre",
    receipt_sha256: "a".repeat(64),
    receipt: {},
    expected_target_generation: 7,
    expected_ownership_generation: 9,
    expected_request_digests: {},
    [field]: "caller-controlled"
  }) + "\n");
  const [code] = await once(child, "exit");
  return { code, payload: JSON.parse(stdout.trim()) };
}

function issueReceipts(secret, bindingSha256, now) {
  const machineContext = {
    conversation_id: "6a9e4158-cc8c-83ea-9763-05a7fd51f597",
    browser_target_id: "browser-target-v2-cli",
    top_frame_id: "top-frame-v2-cli",
    loader_id: "loader-v2-cli",
    generation_anchor_sha256: "c".repeat(64),
    secure_origin: "https://chatgpt.com",
    extension_binding_sha256: bindingSha256,
    extension_instance_id_sha256: "d".repeat(64),
    extension_loaded_at_ms: now - 100,
    entry_scope: "runtime_invocation",
    machine_source: "host_invocation_context_v1",
    observed_at_ms: now - 50
  };
  const binding = {
    request_digest_profile: "lab_run_command_digest_v1",
    bridge_call_id: "bc_v2-cli",
    bridge_challenge_nonce: "challenge-v2-cli",
    transport_binding_id: "tb_v2-cli",
    workspace: "/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge",
    production_release_revision: "a".repeat(40),
    production_endpoint_generation: 1,
    bridge_principal_id: "bridge-principal-v2-cli",
    bridge_execution_session_id: "bridge-session-v2-cli",
    bridge_capability_id: "bridge-capability-v2-cli",
    bridge_workspace_id: "local-agent-bridge-production",
    tool_name: "run_command",
    outer_request_sha256: "1".repeat(64),
    forwarded_request_sha256: "2".repeat(64),
    normalized_request_sha256: "3".repeat(64),
    machine_context: machineContext
  };
  const journal = new ToolInvocationReceiptJournal(secret, { clock: () => now });
  const capability = journal.issueCapability(binding);
  const consume = {
    ...binding,
    retry_outer_request_sha256: "4".repeat(64),
    capability,
    host_tool_execution_id: "hte_v2-cli"
  };
  const pre = journal.consumeCapability(consume);
  const terminal = journal.terminalize({
    ...binding,
    retry_outer_request_sha256: consume.retry_outer_request_sha256,
    pre_receipt: pre,
    backend_route: "/v1/tools/run_command",
    http_status: 200,
    response_body_sha256: "5".repeat(64),
    terminal_classification: "SUCCESS"
  });
  const currentEntryContext = { ...machineContext };
  delete currentEntryContext.extension_binding_sha256;
  delete currentEntryContext.extension_instance_id_sha256;
  delete currentEntryContext.extension_loaded_at_ms;
  return { binding, machineContext, pre, terminal, currentEntryContext };
}

async function invokeV2Receipt(t, input, fixture) {
  const root = mkdtempSync(join(tmpdir(), "lab-runtime-verifier-v2-success-"));
  const socketPath = join(root, "host.sock");
  const keyPath = join(root, "attestation.key");
  writeFileSync(keyPath, fixture.secret, { mode: 0o600 });
  chmodSync(keyPath, 0o600);
  const server = await new Promise((resolve, reject) => {
    const listener = net.createServer((socket) => {
      let buffer = "";
      socket.on("data", (chunk) => {
        buffer += chunk.toString("utf8");
        const newline = buffer.indexOf("\n");
        if (newline < 0) return;
        const request = JSON.parse(buffer.slice(0, newline));
        const current_entry = signCurrentEntryEvidence({
          secret: fixture.secret,
          machineContext: fixture.currentEntryContext,
          targetGeneration: request.target_generation,
          ownershipGeneration: request.ownership_generation,
          clock: () => fixture.now
        });
        socket.end(JSON.stringify({
          request_id: request.request_id,
          ok: true,
          result: {
            current_entry,
            extension_epoch: {
              binding_sha256: fixture.bindingSha256,
              instance_id_sha256: fixture.machineContext.extension_instance_id_sha256,
              loaded_at_ms: fixture.machineContext.extension_loaded_at_ms
            }
          }
        }) + "\n");
      });
    });
    listener.once("error", reject);
    listener.listen(socketPath, () => resolve(listener));
  });
  t.after(() => {
    server.close();
    rmSync(root, { recursive: true, force: true });
  });
  const child = spawn(process.execPath, [CLI], {
    cwd: CLI_CWD,
    env: {
      ...process.env,
      LAB_HOST_ATTESTATION_KEY_FILE: keyPath,
      LAB_HOST_ADAPTER_SOCKET: socketPath,
      LAB_HOST_EXTENSION_BINDING_FILE: fixture.bindingPath
    },
    stdio: ["pipe", "pipe", "pipe"]
  });
  let stdout = "";
  child.stdout.on("data", (chunk) => { stdout += chunk.toString("utf8"); });
  child.stdin.end(JSON.stringify(input) + "\n");
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

test("Runtime verifier CLI v2 rejects caller current-entry and Runtime identity fields before Host work", async function (t) {
  for (const field of ["current_entry", "controller_id", "turn_id", "runtime_controller", "runtime_turn"]) {
    const result = await invokeV2WithCallerIdentity(t, field);
    assert.equal(result.code, 1);
    assert.match(result.payload.error, /v2 Runtime Host command keys are invalid/);
  }
});

test("Runtime verifier CLI v2 verifies literal pre and terminal receipts through one fresh Host request", async function (t) {
  const { bindingPath } = createBindingFixture(t);
  const now = Date.now();
  const secret = "v2-cli-test-secret-0123456789abcdef0123456789abcdef";
  const fixture = { secret, now, bindingPath, bindingSha256: sha256(bindingPath) };
  const receipts = issueReceipts(secret, fixture.bindingSha256, now);
  Object.assign(fixture, receipts);
  const expected_request_digests = {
    outer_request_sha256: receipts.pre.outer_request_sha256,
    forwarded_request_sha256: receipts.pre.forwarded_request_sha256,
    normalized_request_sha256: receipts.pre.normalized_request_sha256,
    retry_outer_request_sha256: receipts.pre.retry_outer_request_sha256
  };
  const pre = await invokeV2Receipt(t, {
    protocol: "runtime_host_verifier_cli_v2",
    operation: "verify_tool_pre",
    receipt_sha256: receiptSha256(receipts.pre),
    receipt: receipts.pre,
    expected_target_generation: 7,
    expected_ownership_generation: 9,
    expected_request_digests
  }, fixture);
  assert.equal(pre.code, 0);
  assert.deepEqual(pre.payload, {
    ok: true,
    protocol: "runtime_host_verifier_cli_v2",
    operation: "verify_tool_pre",
    receipt_sha256: receiptSha256(receipts.pre),
    receipt_id: receipts.pre.pre_receipt_id,
    capability_id: receipts.pre.capability_id,
    host_tool_execution_id: receipts.pre.host_tool_execution_id,
    current_entry: pre.payload.current_entry
  });
  const terminal = await invokeV2Receipt(t, {
    protocol: "runtime_host_verifier_cli_v2",
    operation: "verify_tool_terminal",
    receipt_sha256: receiptSha256(receipts.terminal),
    receipt: receipts.terminal,
    expected_target_generation: 7,
    expected_ownership_generation: 9,
    expected_request_digests,
    expected_pre_chain: {
      pre_receipt_id: receipts.pre.pre_receipt_id,
      pre_receipt_sha256: receiptSha256(receipts.pre),
      capability_id: receipts.pre.capability_id,
      host_tool_execution_id: receipts.pre.host_tool_execution_id
    }
  }, fixture);
  assert.equal(terminal.code, 0);
  assert.equal(terminal.payload.receipt_id, receipts.terminal.terminal_receipt_id);
  assert.deepEqual(terminal.payload.pre_chain, {
    pre_receipt_id: receipts.pre.pre_receipt_id,
    pre_receipt_sha256: receiptSha256(receipts.pre)
  });
});
