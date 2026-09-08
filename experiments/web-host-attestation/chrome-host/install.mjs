#!/usr/bin/env node
import { createHash, randomBytes, randomUUID } from "node:crypto";
import {
  chmodSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync
} from "node:fs";
import { homedir } from "node:os";
import { dirname, isAbsolute, join } from "node:path";
import { fileURLToPath } from "node:url";
import { extensionIdFromManifestKey } from "./extension-id.mjs";

const HOST_NAME = "com.local_agent_bridge.web_host";
const EXTENSION_ID_RE = /^[a-p]{32}$/;
const EXTENSION_MANIFEST_PATH = fileURLToPath(new URL("./extension/manifest.json", import.meta.url));
const RUNTIME_VERIFIER_PATH = fileURLToPath(new URL("./runtime-verifier-cli.mjs", import.meta.url));
const RUNTIME_VERIFIER_BUNDLE_PATHS = Object.freeze([
  RUNTIME_VERIFIER_PATH,
  fileURLToPath(new URL("./integration.mjs", import.meta.url)),
  fileURLToPath(new URL("./client.mjs", import.meta.url)),
  fileURLToPath(new URL("../provider.mjs", import.meta.url)),
  fileURLToPath(new URL("../attestation.mjs", import.meta.url)),
  fileURLToPath(new URL("./native-host.mjs", import.meta.url)),
  fileURLToPath(new URL("./native-host-core.mjs", import.meta.url)),
  fileURLToPath(new URL("./native-framing.mjs", import.meta.url)),
  fileURLToPath(new URL("./extension/manifest.json", import.meta.url)),
  fileURLToPath(new URL("./extension/background.js", import.meta.url)),
  fileURLToPath(new URL("./extension/debugger-session.js", import.meta.url)),
  fileURLToPath(new URL("./extension/observation.js", import.meta.url)),
  fileURLToPath(new URL("./extension/browser-submit.js", import.meta.url)),
  fileURLToPath(new URL("./extension/submit-page.js", import.meta.url)),
  fileURLToPath(new URL("./extension/submit-journal.js", import.meta.url)),
  fileURLToPath(new URL("./extension/trusted-input.js", import.meta.url))
]);

const BROWSER_MANIFEST_ROOTS = Object.freeze({
  chrome: ["Library", "Application Support", "Google", "Chrome", "NativeMessagingHosts"],
  "chrome-for-testing": ["Library", "Application Support", "Google", "ChromeForTesting", "NativeMessagingHosts"],
  chromium: ["Library", "Application Support", "Chromium", "NativeMessagingHosts"]
});

function currentUid() {
  return typeof process.getuid === "function" ? process.getuid() : null;
}

function ensureDirectory(path, mode, privateOwner) {
  if (!existsSync(path)) mkdirSync(path, { recursive: true, mode: mode });
  const stat = lstatSync(path);
  if (!stat.isDirectory() || stat.isSymbolicLink()) {
    throw new Error("installer directory must be a real directory: " + path);
  }
  if (privateOwner) {
    const uid = currentUid();
    if (uid != null && stat.uid !== uid) throw new Error("installer directory owner mismatch");
    chmodSync(path, mode);
  }
}

function validatePrivateFile(path, minimumBytes) {
  const stat = lstatSync(path);
  if (!stat.isFile() || stat.isSymbolicLink()) throw new Error("private file must be a regular non-symlink");
  const uid = currentUid();
  if (uid != null && stat.uid !== uid) throw new Error("private file owner mismatch");
  if ((stat.mode & 0o077) !== 0) throw new Error("private file permissions must be 0600 or stricter");
  if (readFileSync(path).length < minimumBytes) throw new Error("private file is too short");
}

function atomicJson(path, value) {
  const parent = dirname(path);
  ensureDirectory(parent, 0o755, false);
  const temp = join(parent, "." + randomUUID() + ".tmp");
  writeFileSync(temp, JSON.stringify(value, null, 2) + "\n", { mode: 0o600, flag: "wx" });
  renameSync(temp, path);
  chmodSync(path, 0o600);
}

function sha256File(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function validateOwnedRegularFile(path, executable) {
  if (!isAbsolute(path)) throw new Error("Runtime verifier bundle path must be absolute");
  const stat = lstatSync(path);
  if (!stat.isFile() || stat.isSymbolicLink()) throw new Error("Runtime verifier bundle member must be a regular non-symlink file");
  const uid = currentUid();
  if (uid != null && stat.uid !== uid) throw new Error("Runtime verifier bundle member owner mismatch");
  if (executable && (stat.mode & 0o111) === 0) throw new Error("Runtime verifier executable must be executable");
}

function installRuntimeVerifierRegistration(homeDir, verifierPath, bundlePaths) {
  const executable = String(verifierPath || RUNTIME_VERIFIER_PATH);
  const members = Array.from(new Set((bundlePaths || RUNTIME_VERIFIER_BUNDLE_PATHS).map(String)));
  if (!members.includes(executable)) throw new Error("Runtime verifier bundle must include executable");
  if (members.length > 64) throw new Error("Runtime verifier bundle is too large");
  const bundle = {};
  for (const member of members) {
    validateOwnedRegularFile(member, member === executable);
    bundle[member] = sha256File(member);
  }
  const configDir = join(homeDir, ".codex");
  ensureDirectory(configDir, 0o700, true);
  const configPath = join(configDir, "adaptive-delivery-host-attestation-verifiers.json");
  let config = { schema_version: 1, verifiers: {} };
  if (existsSync(configPath)) {
    validatePrivateFile(configPath, 2);
    config = JSON.parse(readFileSync(configPath, "utf8"));
    if (!config || config.schema_version !== 1 || typeof config.verifiers !== "object" || Array.isArray(config.verifiers)) {
      throw new Error("existing Runtime Host verifier config is invalid");
    }
  }
  config.verifiers.web = {
    protocol: "runtime_host_verifier_cli_v1",
    executable: executable,
    sha256: bundle[executable],
    bundle_sha256: bundle
  };
  atomicJson(configPath, config);
  return configPath;
}

function manifestExtensionId() {
  const manifest = JSON.parse(readFileSync(EXTENSION_MANIFEST_PATH, "utf8"));
  return extensionIdFromManifestKey(manifest.key);
}

export const DEFAULT_EXTENSION_ID = manifestExtensionId();

export function installChromeNativeHost(options) {
  const requestedExtensionId = String(options && options.extensionId || "").trim();
  const extensionId = requestedExtensionId || DEFAULT_EXTENSION_ID;
  const browser = String(options && options.browser || "chrome");
  const homeDir = options && options.homeDir || homedir();
  const userDataDir = options && options.userDataDir
    ? String(options.userDataDir).trim()
    : null;
  const hostScriptPath = options && options.hostScriptPath ||
    fileURLToPath(new URL("./native-host.mjs", import.meta.url));
  const registerRuntimeVerifier = options && options.registerRuntimeVerifier === true;
  const runtimeVerifierPath = options && options.runtimeVerifierPath || RUNTIME_VERIFIER_PATH;
  const runtimeVerifierBundlePaths = options && options.runtimeVerifierBundlePaths || RUNTIME_VERIFIER_BUNDLE_PATHS;

  if (!EXTENSION_ID_RE.test(extensionId)) throw new Error("extension id must be 32 lowercase a-p characters");
  if (extensionId !== DEFAULT_EXTENSION_ID) {
    throw new Error("extension id does not match the manifest public key");
  }
  if (!(browser in BROWSER_MANIFEST_ROOTS)) throw new Error("unsupported browser: " + browser);
  if (userDataDir && !isAbsolute(userDataDir)) {
    throw new Error("browser user-data-dir must be absolute");
  }
  if (!isAbsolute(hostScriptPath)) throw new Error("native Host script path must be absolute");
  const hostStat = lstatSync(hostScriptPath);
  if (!hostStat.isFile() || hostStat.isSymbolicLink()) throw new Error("native Host script must be a regular non-symlink file");

  const stateRoot = join(homeDir, ".local-agent-bridge", "web-host");
  ensureDirectory(stateRoot, 0o700, true);

  const keyPath = join(stateRoot, "attestation.key");
  let keyCreated = false;
  if (!existsSync(keyPath)) {
    writeFileSync(keyPath, randomBytes(32), { mode: 0o600, flag: "wx" });
    chmodSync(keyPath, 0o600);
    keyCreated = true;
  }
  validatePrivateFile(keyPath, 32);

  let manifestDir;
  let manifestScope;
  if (userDataDir) {
    ensureDirectory(userDataDir, 0o700, true);
    manifestDir = join(userDataDir, "NativeMessagingHosts");
    ensureDirectory(manifestDir, 0o700, true);
    manifestScope = "custom_user_data_dir";
  } else {
    manifestDir = join(homeDir, ...BROWSER_MANIFEST_ROOTS[browser]);
    ensureDirectory(manifestDir, 0o755, false);
    manifestScope = "default_user_path";
  }
  const manifestPath = join(manifestDir, HOST_NAME + ".json");
  atomicJson(manifestPath, {
    name: HOST_NAME,
    description: "Local Agent Bridge Web Host native messaging adapter",
    path: hostScriptPath,
    type: "stdio",
    allowed_origins: [
      "chrome-extension://" + extensionId + "/"
    ]
  });

  const runtimeVerifierConfigPath = registerRuntimeVerifier
    ? installRuntimeVerifierRegistration(homeDir, runtimeVerifierPath, runtimeVerifierBundlePaths)
    : null;

  return Object.freeze({
    browser: browser,
    extension_id: extensionId,
    manifest_path: manifestPath,
    manifest_scope: manifestScope,
    browser_user_data_dir: userDataDir,
    key_path: keyPath,
    socket_path: join(stateRoot, "host.sock"),
    runtime_verifier_config_path: runtimeVerifierConfigPath,
    key_created: keyCreated
  });
}

function parseCli(argv) {
  const args = { browser: "chrome" };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--extension-id") {
      args.extensionId = argv[index + 1];
      index += 1;
    } else if (value === "--browser") {
      args.browser = argv[index + 1];
      index += 1;
    } else if (value === "--user-data-dir") {
      args.userDataDir = argv[index + 1];
      index += 1;
    } else if (value === "--register-runtime-verifier") {
      args.registerRuntimeVerifier = true;
    } else {
      throw new Error("unsupported installer argument: " + value);
    }
  }
  return args;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  try {
    const result = installChromeNativeHost(parseCli(process.argv.slice(2)));
    process.stdout.write(JSON.stringify(result, null, 2) + "\n");
  } catch (error) {
    process.stderr.write(String(error && error.message || error) + "\n");
    process.exit(1);
  }
}
