import {
  observationFromDebugger,
  selectExactTarget,
  selectUniqueConversationTarget
} from "./observation.js";

const NATIVE_HOST = "com.local_agent_bridge.web_host";
const CDP_VERSION = "1.3";
const port = chrome.runtime.connectNative(NATIVE_HOST);

async function allTargets() {
  return await chrome.debugger.getTargets();
}

async function withDebugger(target, callback) {
  if (!Number.isInteger(target.tabId)) throw new Error("debugger target has no tabId");
  const debuggee = { tabId: target.tabId };
  await chrome.debugger.attach(debuggee, CDP_VERSION);
  try {
    const targetResult = await chrome.debugger.sendCommand(debuggee, "Target.getTargetInfo", {});
    const frameTree = await chrome.debugger.sendCommand(debuggee, "Page.getFrameTree", {});
    const cdpInfo = targetResult && targetResult.targetInfo;
    const cdpTargetId = cdpInfo && (cdpInfo.targetId || cdpInfo.id);
    if (!cdpInfo || cdpTargetId !== target.id) {
      throw new Error("attached debugger target identity changed");
    }
    const normalizedTarget = {
      id: target.id,
      tabId: target.tabId,
      type: cdpInfo.type,
      url: cdpInfo.url
    };
    return await callback(normalizedTarget, frameTree);
  } finally {
    try {
      await chrome.debugger.detach(debuggee);
    } catch {
    }
  }
}

async function observeConversation(conversationId) {
  const target = selectUniqueConversationTarget(await allTargets(), conversationId);
  return await withDebugger(target, async function (targetInfo, frameTree) {
    return observationFromDebugger({
      targetInfo: targetInfo,
      frameTree: frameTree,
      observedAtMs: Date.now()
    });
  });
}

async function observeTarget(browserTargetId) {
  const target = selectExactTarget(await allTargets(), browserTargetId);
  return await withDebugger(target, async function (targetInfo, frameTree) {
    return observationFromDebugger({
      targetInfo: targetInfo,
      frameTree: frameTree,
      observedAtMs: Date.now()
    });
  });
}

async function focusTarget(browserTargetId) {
  const target = selectExactTarget(await allTargets(), browserTargetId);
  if (!Number.isInteger(target.tabId)) throw new Error("target has no tabId");
  await chrome.tabs.update(target.tabId, { active: true });
  const tab = await chrome.tabs.get(target.tabId);
  if (Number.isInteger(tab.windowId)) {
    await chrome.windows.update(tab.windowId, { focused: true });
  }
  const observation = await observeTarget(browserTargetId);
  return {
    focused: true,
    browser_target_id: browserTargetId,
    observation: observation
  };
}

async function handleCommand(message) {
  const op = String(message && message.op || "");
  if (op === "observe_conversation") {
    return { observation: await observeConversation(message.conversation_id) };
  }
  if (op === "observe_target") {
    return { observation: await observeTarget(message.browser_target_id) };
  }
  if (op === "focus_target") {
    return await focusTarget(message.browser_target_id);
  }
  if (op === "submit_continuation") {
    return {
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: false,
      status: "submit_not_implemented",
      host_submit_receipt: null
    };
  }
  throw new Error("unsupported browser command: " + op);
}

port.onMessage.addListener(function (message) {
  const requestId = String(message && message.request_id || "");
  if (!requestId) return;
  handleCommand(message).then(function (result) {
    port.postMessage({ request_id: requestId, ok: true, result: result });
  }).catch(function (error) {
    port.postMessage({
      request_id: requestId,
      ok: false,
      error: String(error && error.message || error || "browser command failed")
    });
  });
});
