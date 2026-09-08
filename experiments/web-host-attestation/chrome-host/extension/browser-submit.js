import {
  conversationIdFromChatgptUrl,
  selectExactTarget
} from "./observation.js";
import { pageSubmitFunction } from "./submit-page.js";

function requiredText(value, name, max) {
  const limit = max == null ? 8192 : max;
  const text = String(value == null ? "" : value).trim();
  if (!text) throw new Error(name + " is required");
  if (text.length > limit) throw new Error(name + " exceeds limit");
  return text;
}

function preDispatch(status, retryable) {
  return {
    dispatch_attempted: false,
    submit_confirmed: false,
    retryable: Boolean(retryable),
    status: status,
    host_submit_receipt: null
  };
}

function resultUnknown(receiptFactory) {
  return {
    dispatch_attempted: true,
    submit_confirmed: false,
    retryable: false,
    status: "result_unknown",
    host_submit_receipt: "chrome_submit_" + receiptFactory()
  };
}

export async function submitContinuationThroughChrome(options) {
  const chromeApi = options && options.chromeApi;
  const getTargets = options && options.getTargets;
  const observeTarget = options && options.observeTarget;
  const message = options && options.message || {};
  const receiptFactory = options && options.receiptFactory || function () {
    return globalThis.crypto.randomUUID();
  };

  if (!chromeApi || typeof chromeApi.scripting?.executeScript !== "function") {
    throw new Error("chrome.scripting.executeScript is required");
  }
  if (typeof getTargets !== "function" || typeof observeTarget !== "function") {
    throw new Error("browser machine observation methods are required");
  }

  const browserTargetId = requiredText(message.browser_target_id, "browser_target_id", 256);
  const conversationId = requiredText(message.conversation_id, "conversation_id", 128);
  const payload = requiredText(message.payload, "payload", 8000);

  let target;
  try {
    target = selectExactTarget(await getTargets(), browserTargetId);
    if (conversationIdFromChatgptUrl(target.url) !== conversationId) {
      return preDispatch("wrong_conversation_before_dispatch", false);
    }
    const observation = await observeTarget(browserTargetId);
    if (!observation ||
        observation.browser_target_id !== browserTargetId ||
        observation.conversation_id !== conversationId) {
      return preDispatch("machine_target_changed_before_dispatch", false);
    }
  } catch {
    return preDispatch("machine_target_unavailable_before_dispatch", false);
  }

  if (!Number.isInteger(target.tabId)) {
    return preDispatch("target_tab_unavailable_before_dispatch", false);
  }

  let executionResults;
  try {
    executionResults = await chromeApi.scripting.executeScript({
      target: { tabId: target.tabId },
      world: "ISOLATED",
      func: pageSubmitFunction,
      args: [payload, conversationId]
    });
  } catch {
    return resultUnknown(receiptFactory);
  }

  if (!Array.isArray(executionResults) || executionResults.length !== 1) {
    return resultUnknown(receiptFactory);
  }

  const result = executionResults[0] && executionResults[0].result;
  if (!result ||
      typeof result.dispatch_attempted !== "boolean" ||
      typeof result.submit_confirmed !== "boolean" ||
      (result.submit_confirmed && !result.dispatch_attempted)) {
    return resultUnknown(receiptFactory);
  }

  return {
    dispatch_attempted: result.dispatch_attempted,
    submit_confirmed: result.submit_confirmed,
    retryable: result.dispatch_attempted ? false : Boolean(result.retryable),
    status: requiredText(result.status, "page submit status", 128),
    host_submit_receipt: result.dispatch_attempted
      ? "chrome_submit_" + receiptFactory()
      : null
  };
}
