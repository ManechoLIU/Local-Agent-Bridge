export const MAX_NATIVE_FRAME_BYTES = 64 * 1024;

export function encodeNativeMessage(value, maxBytes) {
  const limit = maxBytes == null ? MAX_NATIVE_FRAME_BYTES : maxBytes;
  const body = Buffer.from(JSON.stringify(value), "utf8");
  if (body.length < 2 || body.length > limit) throw new Error("native message exceeds limit");
  const prefix = Buffer.allocUnsafe(4);
  prefix.writeUInt32LE(body.length, 0);
  return Buffer.concat([prefix, body]);
}

export function consumeNativeFrames(input, maxBytes) {
  const limit = maxBytes == null ? MAX_NATIVE_FRAME_BYTES : maxBytes;
  let buffer = Buffer.from(input);
  const messages = [];
  while (buffer.length >= 4) {
    const length = buffer.readUInt32LE(0);
    if (length < 2 || length > limit) throw new Error("invalid native message length");
    if (buffer.length < 4 + length) break;
    const body = buffer.subarray(4, 4 + length);
    let value;
    try {
      value = JSON.parse(body.toString("utf8"));
    } catch {
      throw new Error("native message is not valid JSON");
    }
    messages.push(value);
    buffer = buffer.subarray(4 + length);
  }
  return { messages: messages, remainder: buffer };
}
