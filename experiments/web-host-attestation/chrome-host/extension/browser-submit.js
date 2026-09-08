import {
  conversationIdFromChatgptUrl,
  selectExactTarget
} from "./observation.js";
import {
  pagePrepareSubmitFunction,
  pageConfirmSubmitFunction
} from "./submit-page.js";

function requiredText(value, name, max) {
  const limit = max == null ? 8192 : max;
  const text = String(value == null ? "" : value).trim();
  if (!text) throw new Error(name + " is required");
  if (text.length > limit) throw new Error(name + " exceeds limit");
  return text;
}

function finiteCoordinate(value, name) {
  const number = Number(value);
  if (!Number.isFinite(number) || number < -100000 || number > 100000) {
    throw new Error(name + " must be a finite browser coordinate");
  }
  return number;
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
  const dispatchTrustedClick = options && options.dispatchTrustedClick;
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
  if (typeof dispatchTrustedClick !== "function") {
    throw new Error("trusted browser input dispatcher is required");
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

  let prepareResults;
  try {
    prepareResults = await chromeApi.scripting.executeScript({
      target: { tabId: target.tabId },
      world: "ISOLATED",
      func: pagePrepareSubmitFunction,
      args: [payload, conversationId]
    });
  } catch {
    return preDispatch("prepare_result_unavailable", false);
  }

  if (!Array.isArray(prepareResults) || prepareResults.length !== 1) {
    return preDispatch("prepare_result_unavailable", false);
  }

  const prepared = prepareResults[0] && prepareResults[0].result;
  if (!prepared ||
      typeof prepared.dispatch_attempted !== "boolean" ||
      typeof prepared.submit_confirmed !== "boolean" ||
      typeof prepared.ready_to_dispatch !== "boolean") {
    return preDispatch("prepare_result_invalid", false);
  }
  if (!prepared.ready_to_dispatch) {
    return preDispatch(
      requiredText(prepared.status, "page prepare status", 128),
      Boolean(prepared.retryable)
    );
  }

  let clickPoint;
  try {
    clickPoint = {
      x: finiteCoordinate(prepared.click_point && prepared.click_point.x, "click_point.x"),
      y: finiteCoordinate(prepared.click_point && prepared.click_point.y, "click_point.y")
    };
  } catch {
    return preDispatch("send_button_not_clickable", false);
  }

  try {
    const beforeDispatch = await observeTarget(browserTargetId);
    if (!beforeDispatch ||
        beforeDispatch.browser_target_id !== browserTargetId ||
        beforeDispatch.conversation_id !== conversationId) {
      return preDispatch("machine_target_changed_before_trusted_dispatch", false);
    }
    await dispatchTrustedClick(browserTargetId, clickPoint);
  } catch {
    return resultUnknown(receiptFactory);
  }

  let confirmationResults;
  try {
    confirmationResults = await chromeApi.scripting.executeScript({
      target: { tabId: target.tabId },
      world: "ISOLATED",
      func: pageConfirmSubmitFunction,
      args: [payload, conversationId, 4000]
    });
  } catch {
    return resultUnknown(receiptFactory);
  }

  if (!Array.isArray(confirmationResults) || confirmationResults.length !== 1) {
    return resultUnknown(receiptFactory);
  }

  const result = confirmationResults[0] && confirmationResults[0].result;
  if (!result ||
      typeof result.dispatch_attempted !== "boolean" ||
      typeof result.submit_confirmed !== "boolean" ||
      (result.submit_confirmed && !result.dispatch_attempted)) {
    return resultUnknown(receiptFactory);
  }

  return {
    dispatch_attempted: true,
    submit_confirmed: result.submit_confirmed,
    retryable: false,
    status: result.submit_confirmed
      ? "submit_confirmed"
      : "result_unknown",
    host_submit_receipt: "chrome_submit_" + receiptFactory()
  };
}
