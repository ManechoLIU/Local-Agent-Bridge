#!/usr/bin/env node
import { randomBytes, randomUUID } from "node:crypto";
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

const HOST_NAME = "com.local_agent_bridge.web_host";
const EXTENSION_ID_RE = /^[a-p]{32}$/;

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

export function installChromeNativeHost(options) {
  const extensionId = String(options && options.extensionId || "").trim();
  const browser = String(options && options.browser || "chrome");
  const homeDir = options && options.homeDir || homedir();
  const hostScriptPath = options && options.hostScriptPath ||
    fileURLToPath(new URL("./native-host.mjs", import.meta.url));

  if (!EXTENSION_ID_RE.test(extensionId)) throw new Error("extension id must be 32 lowercase a-p characters");
  if (!(browser in BROWSER_MANIFEST_ROOTS)) throw new Error("unsupported browser: " + browser);
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

  const manifestDir = join(homeDir, ...BROWSER_MANIFEST_ROOTS[browser]);
  ensureDirectory(manifestDir, 0o755, false);
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

  return Object.freeze({
    browser: browser,
    extension_id: extensionId,
    manifest_path: manifestPath,
    key_path: keyPath,
    socket_path: join(stateRoot, "host.sock"),
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
