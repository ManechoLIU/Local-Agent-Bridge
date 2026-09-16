import net from "node:net";
import { randomUUID } from "node:crypto";
import { RUNTIME_HOST_REQUEST_TIMEOUT_MS } from "./extension/timeout-budget.js";

const MAX_RESPONSE_BYTES = 128 * 1024;

function requiredText(value, name, max) {
  const limit = max == null ? 8192 : max;
  const text = String(value == null ? "" : value).trim();
  if (!text) throw new Error(name + " is required");
  if (text.length > limit) throw new Error(name + " exceeds limit");
  return text;
}

export async function requestNativeHost(socketPath, request, options) {
  const path = requiredText(socketPath, "socketPath", 4096);
  const timeoutMs = options && options.timeoutMs || RUNTIME_HOST_REQUEST_TIMEOUT_MS;
  const requestId = "hr_" + randomUUID();
  return await new Promise(function (resolve, reject) {
    const socket = net.createConnection({ path: path });
    let buffer = Buffer.alloc(0);
    let finished = false;
    const timer = setTimeout(function () {
      finish(new Error("Host adapter request timed out"));
    }, timeoutMs);

    function finish(error, value) {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      socket.destroy();
      if (error) reject(error);
      else resolve(value);
    }

    socket.on("connect", function () {
      const envelope = Object.assign({}, request, {
        request_id: requestId
      });
      socket.write(JSON.stringify(envelope) + "\n");
    });

    socket.on("data", function (chunk) {
      buffer = Buffer.concat([buffer, chunk]);
      if (buffer.length > MAX_RESPONSE_BYTES) {
        finish(new Error("Host adapter response exceeds limit"));
        return;
      }
      const newline = buffer.indexOf(0x0a);
      if (newline < 0) return;
      let response;
      try {
        response = JSON.parse(buffer.subarray(0, newline).toString("utf8"));
      } catch {
        finish(new Error("Host adapter returned invalid JSON"));
        return;
      }
      if (response.request_id !== requestId) {
        finish(new Error("Host adapter response request_id mismatch"));
        return;
      }
      if (response.ok !== true) {
        finish(new Error(String(response.error || "Host adapter request failed")));
        return;
      }
      finish(null, response.result);
    });

    socket.on("error", function (error) {
      finish(error);
    });
  });
}

export function createChromeHostDriver(options) {
  const socketPath = options && options.socketPath;
  const timeoutMs = options && options.timeoutMs || RUNTIME_HOST_REQUEST_TIMEOUT_MS;
  const expectedBindingSha256 = options && options.expectedExtensionBindingSha256 || null;

  function requireExpectedExtensionEpoch(result) {
    if (expectedBindingSha256 == null) return;
    if (!/^[0-9a-f]{64}$/.test(expectedBindingSha256)) {
      throw new Error("expected extension binding hash is invalid");
    }
    const epoch = result && result.extension_epoch;
    if (!epoch || epoch.binding_sha256 !== expectedBindingSha256 ||
        !/^[0-9a-f]{64}$/.test(String(epoch.instance_id_sha256 || "")) ||
        !Number.isSafeInteger(epoch.loaded_at_ms) || epoch.loaded_at_ms < 1) {
      throw new Error("Host adapter extension epoch does not match current binding");
    }
  }

  async function discoverCurrentEntryWithEpoch(input) {
    const result = await requestNativeHost(socketPath, {
      op: "discover_current_entry",
      target_generation: Number(input && input.target_generation),
      ownership_generation: Number(input && input.ownership_generation)
    }, { timeoutMs: timeoutMs });
    requireExpectedExtensionEpoch(result);
    return Object.freeze({
      current_entry: result.current_entry,
      extension_epoch: Object.freeze({
        binding_sha256: result.extension_epoch.binding_sha256,
        instance_id_sha256: result.extension_epoch.instance_id_sha256,
        loaded_at_ms: result.extension_epoch.loaded_at_ms
      })
    });
  }

  return Object.freeze({
    interface_version: 1,

    async discoverCurrentEntry(input) {
      return (await discoverCurrentEntryWithEpoch(input)).current_entry;
    },

    discoverCurrentEntryWithEpoch,

    async attest(challenge) {
      const result = await requestNativeHost(socketPath, {
        op: "attest",
        challenge: challenge
      }, { timeoutMs: timeoutMs });
      requireExpectedExtensionEpoch(result);
      return result.attestation;
    },

    async observeTarget(browserTargetId) {
      const result = await requestNativeHost(socketPath, {
        op: "observe_target",
        browser_target_id: requiredText(browserTargetId, "browser_target_id", 256)
      }, { timeoutMs: timeoutMs });
      requireExpectedExtensionEpoch(result);
      return result.observation;
    },

    async submitContinuation(input) {
      const result = await requestNativeHost(socketPath, {
        op: "submit_continuation",
        browser_target_id: requiredText(input && input.browser_target_id, "browser_target_id", 256),
        conversation_id: requiredText(input && input.conversation_id, "conversation_id", 128),
        payload: requiredText(input && input.payload, "payload", 8000),
        wake_id: requiredText(input && input.wake_id, "wake_id", 256),
        wake_nonce: requiredText(input && input.wake_nonce, "wake_nonce", 256),
        target_generation: Number(input && input.target_generation),
        ownership_generation: Number(input && input.ownership_generation)
      }, { timeoutMs: timeoutMs });
      requireExpectedExtensionEpoch(result);
      return result;
    },

    async reconcileContinuation(input) {
      const result = await requestNativeHost(socketPath, {
        op: "reconcile_continuation",
        browser_target_id: requiredText(input && input.browser_target_id, "browser_target_id", 256),
        conversation_id: requiredText(input && input.conversation_id, "conversation_id", 128),
        payload_sha256: requiredText(input && input.payload_sha256, "payload_sha256", 64),
        wake_id: requiredText(input && input.wake_id, "wake_id", 256),
        target_generation: Number(input && input.target_generation),
        ownership_generation: Number(input && input.ownership_generation)
      }, { timeoutMs: timeoutMs });
      requireExpectedExtensionEpoch(result);
      return result;
    }
  });
}
