import {
  observationFromDebugger,
  selectExactTarget,
  selectUniqueConversationTarget
} from "./observation.js";
import { submitContinuationThroughChrome } from "./browser-submit.js";
import { withDebuggerSession } from "./debugger-session.js";

const NATIVE_HOST = "com.local_agent_bridge.web_host";

function setNativeDiagnostic(state, error, details) {
  globalThis.__LAB_HOST_NATIVE_STATE = Object.freeze({
    state: state,
    error: error == null ? null : String(error),
    socket_path: details?.socket_path == null ? null : String(details.socket_path),
    updated_at_ms: Date.now()
  });
}

setNativeDiagnostic("connecting", null);
const port = chrome.runtime.connectNative(NATIVE_HOST);
const extensionHelloId = "eh_" + crypto.randomUUID();

port.onDisconnect.addListener(function () {
  const message = chrome.runtime.lastError?.message || "native_port_disconnected";
  setNativeDiagnostic("disconnected", message);
});

try {
  port.postMessage({
    request_id: extensionHelloId,
    op: "extension_hello",
    interface_version: 1,
    extension_id: chrome.runtime.id
  });
  setNativeDiagnostic("handshake_sent", null);
} catch (error) {
  setNativeDiagnostic("disconnected", error?.message || "native_handshake_send_failed");
}

async function allTargets() {
  return await chrome.debugger.getTargets();
}

async function withDebugger(target, callback) {
  return await withDebuggerSession({
    chromeApi: chrome,
    target: target,
    callback: async function (targetResult, frameTree, debuggerSession) {
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
      return await callback(normalizedTarget, frameTree, debuggerSession);
    }
  });
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

async function dispatchTrustedClick(browserTargetId, clickPoint) {
  const target = selectExactTarget(await allTargets(), browserTargetId);
  const x = Number(clickPoint && clickPoint.x);
  const y = Number(clickPoint && clickPoint.y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) {
    throw new Error("trusted click coordinates are invalid");
  }
  return await withDebugger(target, async function (targetInfo, _frameTree, debuggerSession) {
    if (!debuggerSession || typeof debuggerSession.sendCommand !== "function") {
      throw new Error("debugger command session is unavailable");
    }
    if (targetInfo.id !== browserTargetId) {
      throw new Error("trusted click target identity changed");
    }
    await debuggerSession.sendCommand("Input.dispatchMouseEvent", {
      type: "mouseMoved",
      x: x,
      y: y,
      button: "none"
    });
    await debuggerSession.sendCommand("Input.dispatchMouseEvent", {
      type: "mousePressed",
      x: x,
      y: y,
      button: "left",
      buttons: 1,
      clickCount: 1
    });
    await debuggerSession.sendCommand("Input.dispatchMouseEvent", {
      type: "mouseReleased",
      x: x,
      y: y,
      button: "left",
      buttons: 0,
      clickCount: 1
    });
    return { dispatched: true, browser_target_id: browserTargetId };
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
    return await submitContinuationThroughChrome({
      chromeApi: chrome,
      getTargets: allTargets,
      observeTarget: observeTarget,
      dispatchTrustedClick: dispatchTrustedClick,
      message: message
    });
  }
  throw new Error("unsupported browser command: " + op);
}

port.onMessage.addListener(function (message) {
  const requestId = String(message && message.request_id || "");
  if (!requestId) return;
  if (message?.kind === "extension_hello_ack" && requestId === extensionHelloId) {
    if (message.ok === true && message?.result?.status === "native_host_ready") {
      setNativeDiagnostic("connected", null, {
        socket_path: message?.result?.socket_path
      });
    } else {
      setNativeDiagnostic("disconnected", message?.error || "native_handshake_rejected");
    }
    return;
  }
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
