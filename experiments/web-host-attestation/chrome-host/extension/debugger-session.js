const DEFAULT_DEBUGGER_OPERATION_TIMEOUT_MS = 3000;
const DETACH_TIMEOUT_MS = 1000;

function boundedTimeoutMs(value) {
  const number = Number(value == null ? DEFAULT_DEBUGGER_OPERATION_TIMEOUT_MS : value);
  if (!Number.isFinite(number) || number < 250 || number > 10000) {
    throw new Error("debugger timeout must be between 250ms and 10000ms");
  }
  return Math.floor(number);
}

function withTimeout(promise, timeoutMs, message) {
  return new Promise(function (resolve, reject) {
    let settled = false;
    const timer = setTimeout(function () {
      if (settled) return;
      settled = true;
      reject(new Error(message));
    }, timeoutMs);
    Promise.resolve(promise).then(function (value) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(value);
    }, function (error) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    });
  });
}

async function bestEffortDetach(chromeApi, debuggee) {
  try {
    await withTimeout(
      chromeApi.debugger.detach(debuggee),
      DETACH_TIMEOUT_MS,
      "debugger detach timed out"
    );
  } catch {
  }
}

export async function withDebuggerSession(options) {
  const chromeApi = options && options.chromeApi;
  const target = options && options.target;
  const callback = options && options.callback;
  const timeoutMs = boundedTimeoutMs(options && options.timeoutMs);

  if (!chromeApi || typeof chromeApi.debugger?.attach !== "function" ||
      typeof chromeApi.debugger?.sendCommand !== "function" ||
      typeof chromeApi.debugger?.detach !== "function") {
    throw new Error("chrome.debugger API is required");
  }
  if (!target || !Number.isInteger(target.tabId)) {
    throw new Error("debugger target has no tabId");
  }
  if (typeof callback !== "function") throw new Error("debugger callback is required");

  const debuggee = { tabId: target.tabId };
  let attached = false;
  const attachPromise = Promise.resolve(
    chromeApi.debugger.attach(debuggee, "1.3")
  );

  try {
    await withTimeout(attachPromise, timeoutMs, "debugger attach timed out");
    attached = true;

    const targetResult = await withTimeout(
      chromeApi.debugger.sendCommand(debuggee, "Target.getTargetInfo", {}),
      timeoutMs,
      "Target.getTargetInfo timed out"
    );
    const frameTree = await withTimeout(
      chromeApi.debugger.sendCommand(debuggee, "Page.getFrameTree", {}),
      timeoutMs,
      "Page.getFrameTree timed out"
    );
    return await callback(targetResult, frameTree);
  } catch (error) {
    if (!attached) {
      attachPromise.then(function () {
        return bestEffortDetach(chromeApi, debuggee);
      }).catch(function () {});
    }
    throw error;
  } finally {
    if (attached) await bestEffortDetach(chromeApi, debuggee);
  }
}

export const DEBUGGER_OPERATION_TIMEOUT_MS = DEFAULT_DEBUGGER_OPERATION_TIMEOUT_MS;
