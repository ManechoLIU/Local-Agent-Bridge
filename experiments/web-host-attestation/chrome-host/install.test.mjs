import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { installChromeNativeHost } from "./install.mjs";

const EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop";
const HOST_SCRIPT = fileURLToPath(new URL("./native-host.mjs", import.meta.url));

test("installer creates private stable key and exact-origin macOS Chrome manifest", function (t) {
  const home = mkdtempSync(join(tmpdir(), "lab-host-install-"));
  t.after(function () {
    rmSync(home, { recursive: true, force: true });
  });

  const first = installChromeNativeHost({
    extensionId: EXTENSION_ID,
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

test("installer rejects wildcard-like or malformed extension ids", function () {
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
});
