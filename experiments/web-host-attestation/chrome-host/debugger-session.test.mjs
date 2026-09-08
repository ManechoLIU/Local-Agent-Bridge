import test from "node:test";
import assert from "node:assert/strict";
import {
  DEBUGGER_OPERATION_TIMEOUT_MS,
  withDebuggerSession
} from "./extension/debugger-session.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise(function (res, rej) {
    resolve = res; reject = rej;
  });
  return { promise, resolve, reject };
}

test("debugger session times out a stuck CDP command and always detaches", async function () {
  const frameTree = deferred();
  const calls = [];
  const chromeApi = {
    debugger: {
      async attach(debuggee) { calls.push(["attach", debuggee.tabId]); },
      async sendCommand(debuggee, method) {
        calls.push(["send", method]);
        if (method === "Target.getTargetInfo") {
          return { targetInfo: { targetId: "target-1", type: "page", url: "https://chatgpt.com/c/x" } };
        }
        return await frameTree.promise;
      },
      async detach(debuggee) { calls.push(["detach", debuggee.tabId]); }
    }
  };

  await assert.rejects(
    withDebuggerSession({
      chromeApi,
      target: { id: "target-1", tabId: 7 },
      timeoutMs: 30,
      callback() { throw new Error("callback must not run"); }
    }),
    /debugger timeout must be between/
  );

  await assert.rejects(
    withDebuggerSession({
      chromeApi,
      target: { id: "target-1", tabId: 7 },
      timeoutMs: 250,
      callback() { throw new Error("callback must not run"); }
    }),
    /Page.getFrameTree timed out/
  );
  assert.deepEqual(calls.at(-1), ["detach", 7]);
});

test("late debugger attach is detached after the caller timeout", async function () {
  const attach = deferred();
  let detachCount = 0;
  const chromeApi = {
    debugger: {
      async attach() { return await attach.promise; },
      async sendCommand() { throw new Error("must not run"); },
      async detach() { detachCount += 1; }
    }
  };
  const pending = withDebuggerSession({
    chromeApi,
    target: { id: "target-2", tabId: 8 },
    timeoutMs: 250,
    callback() {}
  });
  await assert.rejects(pending, /debugger attach timed out/);
  assert.equal(detachCount, 0);
  attach.resolve();
  await new Promise(function (resolve) { setTimeout(resolve, 0); });
  assert.equal(detachCount, 1);
});

test("default debugger operation timeout remains below the native Host boundary", function () {
  assert.equal(DEBUGGER_OPERATION_TIMEOUT_MS, 3000);
  assert.ok(DEBUGGER_OPERATION_TIMEOUT_MS < 4000);
});
