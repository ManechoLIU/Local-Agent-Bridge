import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { extensionIdFromManifestKey } from "./extension-id.mjs";

const manifestPath = fileURLToPath(new URL("./extension/manifest.json", import.meta.url));

test("manifest public key fixes the expected development extension id", function () {
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  assert.equal(extensionIdFromManifestKey(manifest.key), "ikidefdlcddfomimpckflbajjccnmfna");
});

test("invalid manifest key is rejected", function () {
  assert.throws(function () {
    extensionIdFromManifestKey("");
  }, /required/);
  assert.throws(function () {
    extensionIdFromManifestKey("not-base64-***");
  }, /valid base64 DER/);
});
