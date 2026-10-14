import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const CONVERSATION = "6a9e4158-cc8c-83ea-9763-05a7fd51f597";
const REAL_EXTENSION_MANIFEST = Object.freeze(JSON.parse(readFileSync(
  fileURLToPath(new URL("./extension/manifest.json", import.meta.url)), "utf8"
)));
let backgroundVersion = 0;

function event() {
  const listeners = [];
  return {
    addListener(listener, filter) {
      listeners.push({ listener, filter });
    },
    emit(value) {
      for (const item of listeners) item.listener(value);
    }
  };
}

function storageArea(values, failure = null) {
  return {
    async get(key) {
      if (key == null) return { ...values };
      return Object.hasOwn(values, key) ? { [key]: values[key] } : {};
    },
    async set(entries) {
      if (failure) throw failure;
      Object.assign(values, entries);
    },
    async remove(key) {
      if (failure) throw failure;
      delete values[key];
    }
  };
}

function chromeFixture(options = {}) {
  const listeners = {
    before: event(),
    sendHeaders: event(),
    completed: event(),
    errored: event(),
    message: event(),
    disconnect: event()
  };
  const port = {
    onMessage: listeners.message,
    onDisconnect: listeners.disconnect,
    messages: [],
    postMessage(message) {
      this.messages.push(message);
    }
  };
  const sessionValues = options.sessionValues || {};
  let nativeConnectCalls = 0;
  const storage = {
    local: storageArea({})
  };
  if (options.withoutSession !== true) {
    storage.session = storageArea(sessionValues, options.sessionFailure);
  }
  return {
    chrome: {
      webRequest: {
        onBeforeRequest: listeners.before,
        onBeforeSendHeaders: listeners.sendHeaders,
        onCompleted: listeners.completed,
        onErrorOccurred: listeners.errored
      },
      storage,
      runtime: {
        id: "extension-under-test",
        getManifest() {
          return { version: options.manifestVersion || "0.1.2" };
        },
        connectNative() {
          nativeConnectCalls += 1;
          return port;
        }
      },
      debugger: {
        async getTargets() {
          return [{
            id: "browser-target-7",
            tabId: 9,
            type: "page",
            url: "https://chatgpt.com/c/" + CONVERSATION
          }];
        },
        async attach() {},
        async detach() {},
        async sendCommand(_debuggee, method) {
          if (method === "Target.getTargetInfo") {
            return { targetInfo: {
              id: "browser-target-7",
              type: "page",
              url: "https://chatgpt.com/c/" + CONVERSATION
            } };
          }
          if (method === "Page.getFrameTree") {
            return { frameTree: { frame: {
              id: "frame-top-9",
              loaderId: "loader-doc-11",
              securityOrigin: "https://chatgpt.com",
              url: "https://chatgpt.com/c/" + CONVERSATION
            } } };
          }
          throw new Error("unexpected debugger command: " + method);
        }
      },
      scripting: { async executeScript() { return []; } }
    },
    listeners,
    port,
    sessionValues,
    nativeConnectCalls: () => nativeConnectCalls
  };
}

async function flushAsyncWork() {
  for (let index = 0; index < 4; index += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

async function replyFor(port, requestId) {
  for (let index = 0; index < 20; index += 1) {
    const reply = port.messages.find((message) => message.request_id === requestId);
    if (reply) return reply;
    await new Promise((resolve) => setImmediate(resolve));
  }
  return null;
}

async function loadBackground(t, fixture) {
  const previousChrome = globalThis.chrome;
  const previousDiagnostic = globalThis.__LAB_HOST_NATIVE_STATE;
  const previousRegistryDiagnostic = globalThis.__LAB_HOST_CURRENT_ENTRY_REGISTRY_STATE;
  globalThis.chrome = fixture.chrome;
  delete globalThis.__LAB_HOST_NATIVE_STATE;
  delete globalThis.__LAB_HOST_CURRENT_ENTRY_REGISTRY_STATE;
  t.after(function () {
    globalThis.chrome = previousChrome;
    if (previousDiagnostic === undefined) delete globalThis.__LAB_HOST_NATIVE_STATE;
    else globalThis.__LAB_HOST_NATIVE_STATE = previousDiagnostic;
    if (previousRegistryDiagnostic === undefined) delete globalThis.__LAB_HOST_CURRENT_ENTRY_REGISTRY_STATE;
    else globalThis.__LAB_HOST_CURRENT_ENTRY_REGISTRY_STATE = previousRegistryDiagnostic;
  });
  await import(new URL("./extension/background.js?background_test=" + (++backgroundVersion), import.meta.url));
}

function generationStart() {
  return {
    requestId: "generation-r1",
    tabId: 9,
    method: "POST",
    url: "https://chatgpt.com/backend-api/f/conversation"
  };
}

test("background refuses to start without session storage before Native Host connection", async function (t) {
  const fixture = chromeFixture({ withoutSession: true });
  await assert.rejects(
    loadBackground(t, fixture),
    /chrome\.storage\.session is required for current-entry registry/
  );
  assert.equal(fixture.nativeConnectCalls(), 0);
  assert.deepEqual(fixture.port.messages, []);
});

test("real extension manifest meets the loaded-worker protocol fence", () => {
  assert.equal(REAL_EXTENSION_MANIFEST.version, "0.1.2");
});

test("background hello reports its exact release and current-entry registry protocol", async function (t) {
  const fixture = chromeFixture({ manifestVersion: "0.1.2" });
  await loadBackground(t, fixture);

  assert.deepEqual(fixture.port.messages, [{
    request_id: fixture.port.messages[0].request_id,
    op: "extension_hello",
    interface_version: 1,
    extension_id: "extension-under-test",
    extension_version: "0.1.2",
    current_entry_registry_protocol_version: 2
  }]);
});

test("background restores a current generation from chrome.storage.session after worker restart", async function (t) {
  const sessionValues = {};
  const first = chromeFixture({ sessionValues });
  await loadBackground(t, first);
  first.listeners.before.emit(generationStart());
  await flushAsyncWork();
  assert.equal(Object.keys(sessionValues).length, 1);

  const restarted = chromeFixture({ sessionValues });
  await loadBackground(t, restarted);
  restarted.listeners.message.emit({ request_id: "discover-1", op: "discover_current_entry" });

  const reply = await replyFor(restarted.port, "discover-1");
  assert.ok(reply);
  assert.equal(reply.request_id, "discover-1");
  assert.equal(reply.ok, true);
  const context = reply.result.current_entry_context;
  const { generation_anchor_sha256: generationAnchor, observed_at_ms: observedAt, ...fixedContext } = context;
  assert.deepEqual(fixedContext, {
    entry_scope: "runtime_invocation",
    machine_source: "host_invocation_context_v1",
    conversation_id: CONVERSATION,
    browser_target_id: "browser-target-7",
    top_frame_id: "frame-top-9",
    loader_id: "loader-doc-11",
    secure_origin: "https://chatgpt.com"
  });
  assert.match(generationAnchor, /^[0-9a-f]{64}$/);
  assert.ok(Number.isSafeInteger(observedAt));
});

test("background completion and error events erase session entries before a worker restart", async function (t) {
  for (const eventName of ["completed", "errored"]) {
    await t.test("on" + eventName + " cannot revive a finished generation", async function (t) {
      const sessionValues = {};
      const activeWorker = chromeFixture({ sessionValues });
      await loadBackground(t, activeWorker);
      activeWorker.listeners.before.emit(generationStart());
      await flushAsyncWork();
      assert.equal(Object.keys(sessionValues).length, 1);

      activeWorker.listeners[eventName].emit({ requestId: "generation-r1" });
      await flushAsyncWork();
      assert.deepEqual(sessionValues, {
        lab_current_entry_registry_v1: {
          version: 2,
          active: [],
          pending_stops: []
        }
      });

      const restarted = chromeFixture({ sessionValues });
      await loadBackground(t, restarted);
      restarted.listeners.message.emit({ request_id: "discover-finished", op: "discover_current_entry" });

      const reply = await replyFor(restarted.port, "discover-finished");
      assert.ok(reply);
      assert.equal(reply.ok, false);
      assert.match(reply.error, /HOST_CURRENT_ENTRY_UNAVAILABLE: no active ChatGPT generation/);
    });
  }
});

test("background observes a service worker generation from Referer headers", async function (t) {
  const fixture = chromeFixture();
  await loadBackground(t, fixture);
  fixture.listeners.before.emit({
    requestId: "generation-sw-1",
    tabId: -1,
    method: "POST",
    url: "https://chatgpt.com/backend-api/f/conversation",
    initiator: "https://chatgpt.com"
  });
  await flushAsyncWork();
  fixture.listeners.message.emit({ request_id: "discover-missing", op: "discover_current_entry" });
  const missing = await replyFor(fixture.port, "discover-missing");
  assert.equal(missing.ok, false);
  assert.match(missing.error, /HOST_CURRENT_ENTRY_UNAVAILABLE: no active ChatGPT generation/);

  fixture.listeners.sendHeaders.emit({
    requestId: "generation-sw-1",
    tabId: -1,
    method: "POST",
    url: "https://chatgpt.com/backend-api/f/conversation",
    initiator: "https://chatgpt.com",
    requestHeaders: [{ name: "Referer", value: "https://chatgpt.com/c/" + CONVERSATION }]
  });
  await flushAsyncWork();
  fixture.listeners.message.emit({ request_id: "discover-sw", op: "discover_current_entry" });
  const reply = await replyFor(fixture.port, "discover-sw");
  assert.equal(reply.ok, true);
  assert.equal(reply.result.current_entry_context.conversation_id, CONVERSATION);
  assert.equal(reply.result.current_entry_context.browser_target_id, "browser-target-7");
});

test("background clears service worker stop on completion or error without request headers", async function (t) {
  for (const eventName of ["completed", "errored"]) {
    await t.test("on" + eventName + " clears the bound generation after worker restart", async function (t) {
      const sessionValues = {};
      const first = chromeFixture({ sessionValues });
      await loadBackground(t, first);
      const referer = [{ name: "Referer", value: "https://chatgpt.com/c/" + CONVERSATION }];
      first.listeners.sendHeaders.emit({
        requestId: "generation-sw-2",
        tabId: -1,
        method: "POST",
        url: "https://chatgpt.com/backend-api/f/conversation",
        initiator: "https://chatgpt.com",
        requestHeaders: referer
      });
      first.listeners.sendHeaders.emit({
        requestId: "stop-sw-2",
        tabId: -1,
        method: "POST",
        url: "https://chatgpt.com/backend-api/stop_conversation",
        initiator: "https://chatgpt.com",
        requestHeaders: referer
      });
      await flushAsyncWork();

      const restarted = chromeFixture({ sessionValues });
      await loadBackground(t, restarted);
      restarted.listeners[eventName].emit({
        requestId: "stop-sw-2",
        tabId: -1,
        method: "POST",
        url: "https://chatgpt.com/backend-api/stop_conversation",
        initiator: "https://chatgpt.com"
      });
      await flushAsyncWork();
      restarted.listeners.message.emit({ request_id: "discover-stopped", op: "discover_current_entry" });
      const reply = await replyFor(restarted.port, "discover-stopped");
      assert.equal(reply?.ok, false);
      assert.match(reply.error, /HOST_CURRENT_ENTRY_UNAVAILABLE: no active ChatGPT generation/);
    });
  }
});

test("background invalidates an unattributed service worker stop before headerless completion", async function (t) {
  const sessionValues = {};
  const fixture = chromeFixture({ sessionValues });
  await loadBackground(t, fixture);
  fixture.listeners.sendHeaders.emit({
    requestId: "generation-sw-unattributed",
    tabId: -1,
    method: "POST",
    url: "https://chatgpt.com/backend-api/f/conversation",
    initiator: "https://chatgpt.com",
    requestHeaders: [{ name: "Referer", value: "https://chatgpt.com/c/" + CONVERSATION }]
  });
  await flushAsyncWork();
  fixture.listeners.sendHeaders.emit({
    requestId: "stop-sw-unattributed",
    tabId: -1,
    method: "POST",
    url: "https://chatgpt.com/backend-api/stop_conversation",
    initiator: "https://chatgpt.com",
    requestHeaders: []
  });
  await flushAsyncWork();

  const restarted = chromeFixture({ sessionValues });
  await loadBackground(t, restarted);
  restarted.listeners.completed.emit({
    requestId: "stop-sw-unattributed",
    tabId: -1,
    method: "POST",
    url: "https://chatgpt.com/backend-api/stop_conversation",
    initiator: "https://chatgpt.com"
  });
  await flushAsyncWork();
  restarted.listeners.message.emit({ request_id: "discover-unattributed-stop", op: "discover_current_entry" });
  const reply = await replyFor(restarted.port, "discover-unattributed-stop");
  assert.equal(reply?.ok, false);
  assert.match(reply.error, /HOST_CURRENT_ENTRY_UNAVAILABLE: no active ChatGPT generation/);
});

test("background reports rejected current-entry mutations without an unhandled rejection", async function (t) {
  const fixture = chromeFixture({ sessionFailure: new Error("session write denied") });
  const unhandled = [];
  const onUnhandled = function (reason) { unhandled.push(reason); };
  const previousConsoleError = console.error;
  process.on("unhandledRejection", onUnhandled);
  t.after(function () { process.off("unhandledRejection", onUnhandled); });
  console.error = function () {};
  t.after(function () { console.error = previousConsoleError; });
  await loadBackground(t, fixture);
  fixture.listeners.before.emit(generationStart());
  await flushAsyncWork();

  assert.equal(globalThis.__LAB_HOST_NATIVE_STATE.state, "handshake_sent");
  assert.equal(globalThis.__LAB_HOST_CURRENT_ENTRY_REGISTRY_STATE.state, "degraded");
  assert.match(globalThis.__LAB_HOST_CURRENT_ENTRY_REGISTRY_STATE.error, /current_entry_registry_observeStart_failed: HOST_CURRENT_ENTRY_UNAVAILABLE/);
  assert.deepEqual(unhandled, []);
});
