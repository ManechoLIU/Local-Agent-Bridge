import {
  observationFromDebugger,
  selectExactTarget,
  selectUniqueConversationTarget
} from "./observation.js";
import {
  reconcileContinuationThroughChrome,
  submitContinuationThroughChrome
} from "./browser-submit.js";
import { withDebuggerSession } from "./debugger-session.js";
import {
  createChromeSubmitJournal,
  recoverPreparedSubmitJournals
} from "./submit-journal.js";
import { pageSubmitOperationFunction } from "./submit-page.js";
import { createTrustedInputController } from "./trusted-input.js";
import { createSubmitTransactionQueue } from "./submit-transaction-queue.js";
import { createCurrentEntryRegistry, selectCurrentEntryTarget } from "./current-entry-registry.js";

const NATIVE_HOST = "com.local_agent_bridge.web_host";
const CURRENT_ENTRY_REGISTRY_PROTOCOL_VERSION = 2;
const submitJournal = createChromeSubmitJournal(chrome);
const submitTransactions = createSubmitTransactionQueue();
const currentEntryRegistry = createCurrentEntryRegistry({
  sessionStore: chrome.storage.session,
  listChatGptPages: function () {
    return chrome.debugger.getTargets();
  }
});

const currentEntryRequestFilter = {
  urls: ["https://chatgpt.com/backend-api/*"]
};
function reportCurrentEntryMutationFailure(operation, error) {
  const message = "current_entry_registry_" + operation + "_failed: " +
    String(error && error.message || error || "current-entry mutation failed");
  console.error(message, error);
  globalThis.__LAB_HOST_CURRENT_ENTRY_REGISTRY_STATE = Object.freeze({
    state: "degraded",
    error: message,
    updated_at_ms: Date.now()
  });
}

function observeCurrentEntryMutation(operation, details) {
  let mutation;
  try {
    mutation = currentEntryRegistry[operation](details);
  } catch (error) {
    reportCurrentEntryMutationFailure(operation, error);
    return;
  }
  Promise.resolve(mutation).catch(function (error) {
    reportCurrentEntryMutationFailure(operation, error);
  });
}

chrome.webRequest.onBeforeRequest.addListener(function (details) {
  observeCurrentEntryMutation("observeStart", details);
}, currentEntryRequestFilter);
chrome.webRequest.onBeforeSendHeaders.addListener(function (details) {
  observeCurrentEntryMutation("observeStart", details);
  observeCurrentEntryMutation("observeStopStart", details);
}, currentEntryRequestFilter, ["requestHeaders", "extraHeaders"]);
chrome.webRequest.onCompleted.addListener(function (details) {
  observeCurrentEntryMutation("observeFinish", details);
}, currentEntryRequestFilter);
chrome.webRequest.onErrorOccurred.addListener(function (details) {
  observeCurrentEntryMutation("observeFinish", details);
}, currentEntryRequestFilter);

function setNativeDiagnostic(state, error, details) {
  globalThis.__LAB_HOST_NATIVE_STATE = Object.freeze({
    state,
    error: error == null ? null : String(error),
    socket_path: details?.socket_path == null ? null : String(details.socket_path),
    recovery: details?.recovery == null ? null : details.recovery,
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
    extension_id: chrome.runtime.id,
    extension_version: chrome.runtime.getManifest().version,
    current_entry_registry_protocol_version: CURRENT_ENTRY_REGISTRY_PROTOCOL_VERSION
  });
  setNativeDiagnostic("handshake_sent", null);
} catch (error) {
  setNativeDiagnostic("disconnected", error?.message || "native_handshake_send_failed");
}

async function allTargets() {
  return await chrome.debugger.getTargets();
}

async function executePageOperation(tabId, operation, input) {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    world: "ISOLATED",
    func: pageSubmitOperationFunction,
    args: [operation, input]
  });
  if (!Array.isArray(results) || results.length !== 1) {
    throw new Error(operation + " page result is unavailable");
  }
  return results[0] && results[0].result;
}

async function withDebugger(target, callback) {
  return await withDebuggerSession({
    chromeApi: chrome,
    target,
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

async function discoverCurrentEntryContext() {
  const candidate = await currentEntryRegistry.resolveCurrent();
  if (!candidate) {
    throw new Error("HOST_CURRENT_ENTRY_UNAVAILABLE: no active ChatGPT generation");
  }
  const target = selectCurrentEntryTarget(await allTargets(), candidate);
  const observation = await withDebugger(target, async function (targetInfo, frameTree) {
    return observationFromDebugger({
      targetInfo,
      frameTree,
      observedAtMs: Date.now()
    });
  });
  if (!await currentEntryRegistry.isStillCurrent(candidate)) {
    throw new Error("HOST_CURRENT_ENTRY_UNAVAILABLE: generation ended during machine observation");
  }
  return Object.freeze({
    entry_scope: "runtime_invocation",
    machine_source: "host_invocation_context_v1",
    conversation_id: observation.conversation_id,
    browser_target_id: observation.browser_target_id,
    top_frame_id: observation.top_frame_id,
    loader_id: observation.loader_id,
    secure_origin: observation.secure_origin,
    generation_anchor_sha256: candidate.generation_anchor_sha256,
    observed_at_ms: observation.observed_at_ms
  });
}

async function observeConversation(conversationId) {
  const target = selectUniqueConversationTarget(await allTargets(), conversationId);
  return await withDebugger(target, async function (targetInfo, frameTree) {
    return observationFromDebugger({
      targetInfo,
      frameTree,
      observedAtMs: Date.now()
    });
  });
}

async function observeTarget(browserTargetId) {
  const target = selectExactTarget(await allTargets(), browserTargetId);
  return await withDebugger(target, async function (targetInfo, frameTree) {
    return observationFromDebugger({
      targetInfo,
      frameTree,
      observedAtMs: Date.now()
    });
  });
}

const trustedInput = createTrustedInputController({
  allTargets,
  withDebugger,
  executePageOperation,
  observationFromDebugger,
  selectExactTarget
});
const dispatchTrustedClick = trustedInput.dispatchTrustedClick;
const rollbackPreparedPayloadByHash = trustedInput.rollbackPreparedPayloadByHash;

async function recoverStalePreparedSubmissions() {
  return await recoverPreparedSubmitJournals({
    journal: submitJournal,
    rollbackPreparedByHash: rollbackPreparedPayloadByHash
  });
}

const startupRecovery = submitTransactions.run(async function () {
  return await recoverStalePreparedSubmissions();
});
startupRecovery.then(function (results) {
  if (results.length > 0) {
    setNativeDiagnostic("connected", null, { recovery: results });
  }
}).catch(function (error) {
  setNativeDiagnostic("recovery_degraded", error?.message || "submit_recovery_failed");
});

async function handleCommand(message) {
  const op = String(message && message.op || "");
  if (op === "discover_current_entry" || op === "tool_receipt_issue" || op === "tool_receipt_consume") {
    return await submitTransactions.run(async function () {
      return { current_entry_context: await discoverCurrentEntryContext() };
    });
  }
  if (op === "observe_conversation") {
    return await submitTransactions.run(async function () {
      return { observation: await observeConversation(message.conversation_id) };
    });
  }
  if (op === "observe_target") {
    return await submitTransactions.run(async function () {
      return { observation: await observeTarget(message.browser_target_id) };
    });
  }
  if (op === "submit_continuation") {
    return await submitTransactions.run(async function () {
      await recoverStalePreparedSubmissions();
      return await submitContinuationThroughChrome({
        chromeApi: chrome,
        getTargets: allTargets,
        observeTarget,
        dispatchTrustedClick,
        rollbackPreparedByHash: rollbackPreparedPayloadByHash,
        journal: submitJournal,
        message
      });
    });
  }
  if (op === "reconcile_continuation") {
    return await submitTransactions.run(async function () {
      return await reconcileContinuationThroughChrome({
        chromeApi: chrome,
        getTargets: allTargets,
        observeTarget,
        journal: submitJournal,
        message
      });
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
    port.postMessage({ request_id: requestId, ok: true, result });
  }).catch(function (error) {
    port.postMessage({
      request_id: requestId,
      ok: false,
      error: String(error && error.message || error || "browser command failed")
    });
  });
});
