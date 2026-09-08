import { randomUUID } from "node:crypto";
import {
  signHostAttestation,
  validateMachineObservation
} from "../attestation.mjs";

export const MAX_RUNTIME_MESSAGE_BYTES = 64 * 1024;
export const MAX_CONTINUATION_CHARS = 8000;

function requiredText(value, name, max) {
  const limit = max == null ? 8192 : max;
  const text = String(value == null ? "" : value).trim();
  if (!text) throw new Error(name + " is required");
  if (text.length > limit) throw new Error(name + " exceeds limit");
  return text;
}

function positiveInteger(value, name) {
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < 1) {
    throw new Error(name + " must be a positive integer");
  }
  return number;
}

export function validateChallenge(input) {
  return Object.freeze({
    schema_version: positiveInteger(input && input.schema_version || 1, "challenge.schema_version"),
    challenge_id: requiredText(input && input.challenge_id, "challenge.challenge_id", 256),
    challenge_nonce: requiredText(input && input.challenge_nonce, "challenge.challenge_nonce", 256),
    host_type: requiredText(input && input.host_type, "challenge.host_type", 64),
    conversation_id: requiredText(input && input.conversation_id, "challenge.conversation_id", 128),
    target_generation: positiveInteger(input && input.target_generation, "challenge.target_generation"),
    ownership_generation: positiveInteger(input && input.ownership_generation, "challenge.ownership_generation"),
    issued_at_ms: positiveInteger(input && input.issued_at_ms, "challenge.issued_at_ms"),
    expires_at_ms: positiveInteger(input && input.expires_at_ms, "challenge.expires_at_ms")
  });
}

function validateBrowserResponse(response, requestId) {
  if (!response || typeof response !== "object") throw new Error("browser machine response is required");
  if (response.request_id !== requestId) throw new Error("browser machine response request_id mismatch");
  if (response.ok !== true) {
    throw new Error(requiredText(response.error, "browser machine error", 1024));
  }
  return response.result;
}

export function createNativeHostHandler(options) {
  const secret = options && options.secret;
  const sendBrowserCommand = options && options.sendBrowserCommand;
  const clock = options && options.clock || Date.now;
  if (!secret || Buffer.byteLength(secret) < 32) {
    throw new Error("Host attestation key must be at least 32 bytes");
  }
  if (typeof sendBrowserCommand !== "function") {
    throw new Error("sendBrowserCommand is required");
  }

  async function browser(op, fields) {
    const requestId = "hb_" + randomUUID();
    const response = await sendBrowserCommand(Object.assign({
      request_id: requestId,
      op: op
    }, fields || {}));
    return validateBrowserResponse(response, requestId);
  }

  return async function handleRuntimeRequest(request) {
    if (!request || typeof request !== "object" || Array.isArray(request)) {
      throw new Error("runtime request must be an object");
    }
    const op = requiredText(request.op, "op", 64);

    if (op === "health") {
      return { status: "ready", interface_version: 1, submit_implemented: false };
    }

    if (op === "attest") {
      const challenge = validateChallenge(request.challenge);
      if (challenge.host_type !== "chatgpt_web") throw new Error("unsupported challenge host_type");
      const browserResult = await browser("observe_conversation", {
        conversation_id: challenge.conversation_id
      });
      const observation = validateMachineObservation(browserResult && browserResult.observation);
      if (observation.conversation_id !== challenge.conversation_id) {
        throw new Error("browser machine observation conversation mismatch");
      }
      return {
        attestation: signHostAttestation({
          secret: secret,
          challenge: challenge,
          observation: observation,
          clock: clock
        })
      };
    }

    if (op === "observe_target") {
      const target = requiredText(request.browser_target_id, "browser_target_id", 256);
      const browserResult = await browser("observe_target", {
        browser_target_id: target
      });
      const observation = validateMachineObservation(browserResult && browserResult.observation);
      if (observation.browser_target_id !== target) {
        throw new Error("browser machine returned wrong target");
      }
      return { observation: observation };
    }

    if (op === "focus_target") {
      const target = requiredText(request.browser_target_id, "browser_target_id", 256);
      const browserResult = await browser("focus_target", {
        browser_target_id: target
      });
      const observation = validateMachineObservation(browserResult && browserResult.observation);
      if (observation.browser_target_id !== target) {
        throw new Error("browser machine focus returned wrong target");
      }
      return {
        focused: browserResult && browserResult.focused === true,
        observation: observation
      };
    }

    if (op === "submit_continuation") {
      const target = requiredText(request.browser_target_id, "browser_target_id", 256);
      const conversation = requiredText(request.conversation_id, "conversation_id", 128);
      const payload = requiredText(request.payload, "payload", MAX_CONTINUATION_CHARS);
      const wakeId = requiredText(request.wake_id, "wake_id", 256);
      const wakeNonce = requiredText(request.wake_nonce, "wake_nonce", 256);
      const result = await browser("submit_continuation", {
        browser_target_id: target,
        conversation_id: conversation,
        payload: payload,
        wake_id: wakeId,
        wake_nonce: wakeNonce
      });
      if (typeof (result && result.dispatch_attempted) !== "boolean" ||
          typeof (result && result.submit_confirmed) !== "boolean") {
        throw new Error("browser submit receipt flags are required");
      }
      if (result.submit_confirmed && !result.dispatch_attempted) {
        throw new Error("browser submit cannot be confirmed before dispatch");
      }
      return {
        dispatch_attempted: result.dispatch_attempted,
        submit_confirmed: result.submit_confirmed,
        retryable: Boolean(result.retryable),
        status: result.status == null ? null : requiredText(result.status, "submit status", 128),
        host_submit_receipt: result.host_submit_receipt == null
          ? null
          : requiredText(result.host_submit_receipt, "host_submit_receipt", 512)
      };
    }

    throw new Error("unsupported runtime operation: " + op);
  };
}
