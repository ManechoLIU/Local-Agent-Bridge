#!/usr/bin/env node
import { chmodSync, lstatSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { createChromeRuntimeHostIntegration, executeRuntimeVerifierCommand } from "./integration.mjs";

const MAX_INPUT_BYTES = 64 * 1024;

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
  let integration = null;
  if (String(input?.operation || "").trim() === "attest_and_verify") {
    const root = join(homedir(), ".local-agent-bridge", "web-host");
    const keyPath = process.env.LAB_HOST_ATTESTATION_KEY_FILE || join(root, "attestation.key");
    const socketPath = process.env.LAB_HOST_ADAPTER_SOCKET || join(root, "host.sock");
    integration = createChromeRuntimeHostIntegration({
      secret: readPrivateKey(keyPath),
      socketPath
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
