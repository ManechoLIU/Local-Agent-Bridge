import test from "node:test";
import assert from "node:assert/strict";
import {
  consumeNativeFrames,
  encodeNativeMessage
} from "./native-framing.mjs";

test("native framing round trips multiple and partial messages", function () {
  const first = encodeNativeMessage({ request_id: "a", ok: true });
  const second = encodeNativeMessage({ request_id: "b", ok: false });
  const joined = Buffer.concat([first, second]);
  const partial = consumeNativeFrames(joined.subarray(0, joined.length - 3));
  assert.deepEqual(partial.messages, [{ request_id: "a", ok: true }]);
  assert.ok(partial.remainder.length > 0);

  const full = consumeNativeFrames(joined);
  assert.deepEqual(full.messages, [
    { request_id: "a", ok: true },
    { request_id: "b", ok: false }
  ]);
  assert.equal(full.remainder.length, 0);
});

test("native framing rejects oversized length and invalid JSON", function () {
  assert.throws(function () {
    encodeNativeMessage({ payload: "x".repeat(200) }, 32);
  }, /exceeds limit/);

  const badLength = Buffer.alloc(4);
  badLength.writeUInt32LE(9999, 0);
  assert.throws(function () {
    consumeNativeFrames(badLength, 128);
  }, /invalid native message length/);

  const invalidBody = Buffer.from("{bad", "utf8");
  const prefix = Buffer.alloc(4);
  prefix.writeUInt32LE(invalidBody.length, 0);
  assert.throws(function () {
    consumeNativeFrames(Buffer.concat([prefix, invalidBody]));
  }, /not valid JSON/);
});
