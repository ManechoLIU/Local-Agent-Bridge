import { createHash } from "node:crypto";

const EXTENSION_ID_ALPHABET = "abcdefghijklmnop";

export function extensionIdFromManifestKey(keyValue) {
  const text = String(keyValue == null ? "" : keyValue).trim();
  if (!text) throw new Error("extension manifest key is required");
  let publicKey;
  try {
    publicKey = Buffer.from(text, "base64");
  } catch {
    throw new Error("extension manifest key is not valid base64");
  }
  if (publicKey.length < 32 || publicKey.toString("base64").replace(/=+$/, "") !== text.replace(/=+$/, "")) {
    throw new Error("extension manifest key is not valid base64 DER");
  }
  const digest = createHash("sha256").update(publicKey).digest().subarray(0, 16);
  let result = "";
  for (const byte of digest) {
    result += EXTENSION_ID_ALPHABET[byte >> 4];
    result += EXTENSION_ID_ALPHABET[byte & 15];
  }
  return result;
}
