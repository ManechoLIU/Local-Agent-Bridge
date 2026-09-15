#!/usr/bin/env node
import { chmodSync, lstatSync, readFileSync, realpathSync } from "node:fs";
import { createHash } from "node:crypto";
import { homedir } from "node:os";
import { isAbsolute, join, relative, resolve, sep } from "node:path";
import { createChromeRuntimeHostIntegration, executeRuntimeVerifierCommand } from "./integration.mjs";
import { extensionIdFromManifestKey } from "./extension-id.mjs";

const MAX_INPUT_BYTES = 64 * 1024;
const EXTENSION_BINDING_SCHEMA = "runtime_host_extension_binding_v1";
const MAX_EXTENSION_BUNDLE_MEMBERS = 32;
const MAX_PREFERENCE_PATHS = 32;

function isRecord(value) {
  return value != null && typeof value === "object" && !Array.isArray(value);
}

function currentUid() {
  return typeof process.getuid === "function" ? process.getuid() : null;
}

function validatePrivateRegularFile(path, label, permissionsPolicy) {
  if (!isAbsolute(path)) throw new Error(label + " path must be absolute");
  const stat = lstatSync(path);
  if (!stat.isFile() || stat.isSymbolicLink()) throw new Error(label + " must be a regular non-symlink file");
  const uid = currentUid();
  if (uid != null && stat.uid !== uid) throw new Error(label + " owner mismatch");
  const permissions = stat.mode & 0o777;
  if (permissionsPolicy === "0600" && permissions !== 0o600) {
    throw new Error(label + " permissions must be 0600");
  }
  if (permissionsPolicy === "private" && (permissions & 0o077) !== 0) {
    throw new Error(label + " permissions must be private");
  }
  if (permissionsPolicy === "not_group_or_other_writable" && (permissions & 0o022) !== 0) {
    throw new Error(label + " must not be writable by group or other");
  }
}

function readPrivateJson(path, label, permissionsPolicy) {
  validatePrivateRegularFile(path, label, permissionsPolicy);
  let value;
  try {
    value = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    throw new Error(label + " must contain valid JSON");
  }
  return value;
}

function requireString(value, label) {
  const result = String(value == null ? "" : value).trim();
  if (!result) throw new Error(label + " is required");
  return result;
}

function requireMemberWithinExtension(path, extensionDir, realExtensionDir, label) {
  if (!isAbsolute(path) || path !== resolve(path)) throw new Error(label + " must be an absolute normalized path");
  const relativePath = relative(extensionDir, path);
  if (!relativePath || relativePath === ".." || relativePath.startsWith(".." + sep) || isAbsolute(relativePath)) {
    throw new Error(label + " must be within extension_dir");
  }
  validatePrivateRegularFile(path, label, "not_group_or_other_writable");
  const realPath = realpathSync(path);
  const realRelativePath = relative(realExtensionDir, realPath);
  if (!realRelativePath || realRelativePath === ".." || realRelativePath.startsWith(".." + sep) || isAbsolute(realRelativePath)) {
    throw new Error(label + " resolves outside extension_dir");
  }
}

function validateExtensionBinding(bindingPath) {
  const binding = readPrivateJson(bindingPath, "Runtime Host extension binding", "0600");
  if (!isRecord(binding) || binding.schema_version !== EXTENSION_BINDING_SCHEMA) {
    throw new Error("Runtime Host extension binding schema is invalid");
  }
  const extensionId = requireString(binding.extension_id, "Runtime Host extension binding extension_id");
  requireString(binding.release_revision, "Runtime Host extension binding release_revision");
  if (!Number.isSafeInteger(binding.created_at_unix_ms) || binding.created_at_unix_ms < 0) {
    throw new Error("Runtime Host extension binding created_at_unix_ms is invalid");
  }
  const rawExtensionDir = requireString(binding.extension_dir, "Runtime Host extension binding extension_dir");
  if (!isAbsolute(rawExtensionDir)) throw new Error("Runtime Host extension binding extension_dir must be absolute");
  const extensionDir = resolve(rawExtensionDir);
  const extensionStat = lstatSync(extensionDir);
  if (!extensionStat.isDirectory() || extensionStat.isSymbolicLink()) {
    throw new Error("Runtime Host extension binding extension_dir must be a directory non-symlink");
  }
  const uid = currentUid();
  if (uid != null && extensionStat.uid !== uid) throw new Error("Runtime Host extension binding extension_dir owner mismatch");
  const realExtensionDir = realpathSync(extensionDir);

  if (!Array.isArray(binding.preference_paths) || binding.preference_paths.length < 1 || binding.preference_paths.length > MAX_PREFERENCE_PATHS) {
    throw new Error("Runtime Host extension binding preference_paths must contain 1 to " + MAX_PREFERENCE_PATHS + " paths");
  }
  const seenPreferences = new Set();
  for (const rawPreferencePath of binding.preference_paths) {
    const preferencePath = requireString(rawPreferencePath, "Runtime Host extension preference path");
    if (!isAbsolute(preferencePath)) throw new Error("Runtime Host extension preference path must be absolute");
    const normalizedPreferencePath = resolve(preferencePath);
    if (seenPreferences.has(normalizedPreferencePath)) throw new Error("Runtime Host extension binding preference_paths contains duplicates");
    seenPreferences.add(normalizedPreferencePath);
    const preferences = readPrivateJson(normalizedPreferencePath, "Runtime Host extension preference", "0600");
    const configuredPath = preferences?.extensions?.settings?.[extensionId]?.path;
    if (typeof configuredPath !== "string" || resolve(configuredPath) !== extensionDir) {
      throw new Error("Runtime Host extension preference extension path does not match extension_dir");
    }
  }

  if (!isRecord(binding.bundle_sha256)) throw new Error("Runtime Host extension binding bundle_sha256 is invalid");
  const bundleEntries = Object.entries(binding.bundle_sha256);
  if (bundleEntries.length < 1 || bundleEntries.length > MAX_EXTENSION_BUNDLE_MEMBERS) {
    throw new Error("Runtime Host extension binding bundle must contain 1 to " + MAX_EXTENSION_BUNDLE_MEMBERS + " members");
  }
  const seenMembers = new Set();
  const manifestPath = join(extensionDir, "manifest.json");
  let manifestIncluded = false;
  for (const [rawMemberPath, expectedHash] of bundleEntries) {
    const memberPath = String(rawMemberPath);
    if (!/^[a-f0-9]{64}$/.test(String(expectedHash))) {
      throw new Error("Runtime Host extension binding bundle hash is invalid");
    }
    requireMemberWithinExtension(memberPath, extensionDir, realExtensionDir, "Runtime Host extension bundle member");
    if (seenMembers.has(memberPath)) throw new Error("Runtime Host extension binding bundle contains duplicate members");
    seenMembers.add(memberPath);
    if (memberPath === manifestPath) manifestIncluded = true;
    const actualHash = createHash("sha256").update(readFileSync(memberPath)).digest("hex");
    if (actualHash !== expectedHash) throw new Error("Runtime Host extension bundle hash mismatch");
  }
  if (!manifestIncluded) throw new Error("Runtime Host extension binding bundle must include manifest.json");
  const manifest = readPrivateJson(manifestPath, "Runtime Host extension manifest", "not_group_or_other_writable");
  if (extensionIdFromManifestKey(manifest?.key) !== extensionId) {
    throw new Error("Runtime Host extension binding extension_id does not match manifest key");
  }
  return Object.freeze({
    binding_sha256: createHash("sha256").update(readFileSync(bindingPath)).digest("hex")
  });
}

function readPrivateKey(path) {
  const stat = lstatSync(path);
  if (!stat.isFile() || stat.isSymbolicLink()) throw new Error("Runtime Host key must be a regular non-symlink file");
  if (typeof process.getuid === "function" && stat.uid !== process.getuid()) throw new Error("Runtime Host key owner mismatch");
  if ((stat.mode & 0o077) !== 0) throw new Error("Runtime Host key permissions must be 0600 or stricter");
  chmodSync(path, 0o600);
  const key = readFileSync(path);
  if (key.length < 32) throw new Error("Runtime Host key must contain at least 32 bytes");
  return key;
}

async function readOneJsonLine() {
  let buffer = Buffer.alloc(0);
  for await (const chunk of process.stdin) {
    buffer = Buffer.concat([buffer, chunk]);
    if (buffer.length > MAX_INPUT_BYTES) throw new Error("Runtime Host command exceeds limit");
  }
  const text = buffer.toString("utf8").trim();
  if (!text) throw new Error("Runtime Host command is required");
  return JSON.parse(text);
}

async function main() {
  const input = await readOneJsonLine();
  const extensionBindingPath = String(process.env.LAB_HOST_EXTENSION_BINDING_FILE || "").trim();
  const extensionBinding = extensionBindingPath
    ? validateExtensionBinding(extensionBindingPath)
    : null;
  const operation = String(input?.operation || "").trim();
  let integration = null;
  if (["discover_current_entry", "verify_current_entry", "attest_and_verify", "submit_reentry", "verify_tool_pre", "verify_tool_terminal"].includes(operation)) {
    const root = join(homedir(), ".local-agent-bridge", "web-host");
    const keyPath = process.env.LAB_HOST_ATTESTATION_KEY_FILE || join(root, "attestation.key");
    const socketPath = process.env.LAB_HOST_ADAPTER_SOCKET || join(root, "host.sock");
    integration = createChromeRuntimeHostIntegration({
      secret: readPrivateKey(keyPath),
      socketPath,
      verifierStatePath: process.env.LAB_HOST_VERIFIER_STATE_FILE || join(root, "runtime-verifier-state.json"),
      reentryStatePath: process.env.LAB_HOST_REENTRY_STATE_FILE || join(root, "runtime-reentry-state.json"),
      expectedExtensionBindingSha256: extensionBinding && extensionBinding.binding_sha256
    });
  }
  return await executeRuntimeVerifierCommand(input, { integration });
}

try {
  const result = await main();
  process.stdout.write(JSON.stringify(result) + "\n");
} catch (error) {
  process.stdout.write(JSON.stringify({
    ok: false,
    error_code: "RUNTIME_HOST_VERIFIER_FAILED",
    error: String(error?.message || error || "Runtime Host verifier failed")
  }) + "\n");
  process.exitCode = 1;
}
