import test from "node:test";
import assert from "node:assert/strict";
import {
  createCurrentEntryRegistry,
  isChatGptGenerationRequest
} from "./extension/current-entry-registry.js";

function clock(start = 1800000000000) {
  let now = start;
  return { now: () => now, add: (ms) => (now += ms) };
}

function req(id, tabId, url = "https://chatgpt.com/backend-api/f/conversation", method = "POST") {
  return { requestId: id, tabId, url, method };
}

function createSessionStore(initial = undefined) {
  let values = initial === undefined ? {} : structuredClone(initial);
  return {
    async get(key) {
      return Object.hasOwn(values, key) ? { [key]: structuredClone(values[key]) } : {};
    },
    async set(entries) {
      values = { ...values, ...structuredClone(entries) };
    },
    async remove(key) {
      const { [key]: _removed, ...remaining } = values;
      values = remaining;
    },
    hasEntries() {
      return Object.keys(values).length > 0;
    }
  };
}

function createRegistry(options = {}) {
  return createCurrentEntryRegistry({
    sessionStore: createSessionStore(),
    ...options
  });
}

test("registry recognizes only ChatGPT generation requests", () => {
  assert.equal(isChatGptGenerationRequest(req("r1", 7)), true);
  assert.equal(isChatGptGenerationRequest(req("r2", 7, "https://chatgpt.com/backend-api/conversation")), true);
  assert.equal(isChatGptGenerationRequest(req("r3", 7, "https://chatgpt.com/backend-api/conversations", "GET")), false);
  assert.equal(isChatGptGenerationRequest(req("r4", -1)), false);
  assert.equal(isChatGptGenerationRequest(req("r5", 7, "https://example.com/backend-api/f/conversation")), false);
  assert.equal(isChatGptGenerationRequest(req("r6", 7, "https://chatgpt.com/backend-api/f/conversation/prepare")), true);
  assert.equal(isChatGptGenerationRequest(req("r6b", 7, "https://chatgpt.com/backend-api/conversation/prepare")), true);
  assert.equal(isChatGptGenerationRequest(req("r6c", 7, "https://chatgpt.com/backend-api/f/conversation/resume")), true);
  assert.equal(isChatGptGenerationRequest(req("r6d", 7, "https://chatgpt.com/backend-api/conversation/resume")), true);
  assert.equal(isChatGptGenerationRequest(req("r6e", 7, "https://chatgpt.com/backend-api/f/conversation/prepare", "GET")), false);
  assert.equal(isChatGptGenerationRequest(req("r6f", 7, "https://chatgpt.com/backend-api/conversations", "POST")), false);
  assert.equal(isChatGptGenerationRequest(req("r7", 7, "https://chatgpt.com/backend-api/conversation/init")), false);
  assert.equal(isChatGptGenerationRequest(req("r7b", 7, "https://chatgpt.com/backend-api/f/conversation/init")), false);
  assert.equal(isChatGptGenerationRequest({
    ...req("r8", -1, "https://chatgpt.com/backend-api/f/conversation/prepare"),
    initiator: "https://chatgpt.com"
  }), true);
  assert.equal(isChatGptGenerationRequest({
    ...req("r9", -1),
    initiator: "https://chatgpt.com"
  }), true);
  assert.equal(isChatGptGenerationRequest({
    ...req("r10", -1),
    initiator: "https://example.com"
  }), false);
  assert.equal(isChatGptGenerationRequest({
    ...req("r11", -1),
    originUrl: "https://chatgpt.com"
  }), true);
});

test("generation POST completion keeps the active entry until stop_conversation", async () => {
  const registry = createRegistry();
  const generation = req("r1", 9);
  assert.equal(await registry.observeStart(generation), true);

  assert.equal(await registry.observeFinish(generation), false);
  assert.equal(await registry.observeFinish(req("r1", 9, "https://chatgpt.com/backend-api/f/conversation/prepare")), false);
  assert.equal(await registry.observeFinish(req("r1", 9, "https://chatgpt.com/backend-api/conversation/resume")), false);
  assert.equal(await registry.observeFinish(req("r1", 9, "https://chatgpt.com/backend-api/conversation/init")), false);
  assert.equal((await registry.resolveCurrent()).tab_id, 9);

  assert.equal(await registry.observeFinish(req("stop-1", 9, "https://chatgpt.com/backend-api/stop_conversation")), true);
  assert.equal(await registry.resolveCurrent(), null);
});

test("prepare and resume POSTs stay active after their own HTTP completion", async () => {
  for (const url of [
    "https://chatgpt.com/backend-api/f/conversation/prepare",
    "https://chatgpt.com/backend-api/conversation/prepare",
    "https://chatgpt.com/backend-api/f/conversation/resume",
    "https://chatgpt.com/backend-api/conversation/resume"
  ]) {
    const registry = createRegistry();
    const start = req("prep-1", 4, url);
    assert.equal(await registry.observeStart(start), true);
    assert.equal(await registry.observeFinish(start), false);
    assert.equal((await registry.resolveCurrent()).tab_id, 4);
  }
});

test("unique active generation resolves exact tab and opaque generation anchor", async () => {
  const time = clock();
  const registry = createRegistry({ clock: time.now });
  assert.equal(await registry.observeStart(req("r1", 11)), true);
  const candidate = await registry.resolveCurrent();
  assert.equal(candidate.tab_id, 11);
  assert.match(candidate.generation_anchor_sha256, /^[0-9a-f]{64}$/);
  assert.equal(Object.hasOwn(candidate, "generation_request_ids"), false);
  assert.equal(await registry.isStillCurrent(candidate), true);
  registry.observeFinish({ requestId: "r1" });
  assert.equal(await registry.resolveCurrent(), null);
  assert.equal(await registry.isStillCurrent(candidate), false);
});

test("multiple requests in same tab produce a sorted stable generation anchor", async () => {
  const registry = createRegistry();
  registry.observeStart(req("r1", 9));
  registry.observeStart(req("r2", 9, "https://chatgpt.com/backend-api/conversation"));
  const candidate = await registry.resolveCurrent();
  assert.equal(candidate.tab_id, 9);
  const reordered = createRegistry();
  reordered.observeStart(req("r2", 9, "https://chatgpt.com/backend-api/conversation"));
  reordered.observeStart(req("r1", 9));
  assert.equal(candidate.generation_anchor_sha256, (await reordered.resolveCurrent()).generation_anchor_sha256);
});

test("concurrent generations in different tabs fail closed", async () => {
  const registry = createRegistry();
  registry.observeStart(req("r1", 9));
  registry.observeStart(req("r2", 10));
  await assert.rejects(registry.resolveCurrent(), /AMBIGUOUS/);
});

test("stale unfinished request is pruned instead of becoming current entry", async () => {
  const time = clock();
  const registry = createRegistry({ clock: time.now, maxAgeMs: 1000 });
  registry.observeStart(req("r1", 9));
  time.add(1001);
  assert.equal(await registry.resolveCurrent(), null);
});

test("session store restores an active generation after a registry rebuild", async () => {
  const store = createSessionStore();
  const first = createCurrentEntryRegistry({ sessionStore: store });
  assert.equal(await first.observeStart(req("r1", 9)), true);

  const rebuilt = createCurrentEntryRegistry({ clock: () => Date.now(), sessionStore: store });
  const candidate = await rebuilt.resolveCurrent();
  assert.equal(candidate?.tab_id, 9);
  assert.match(candidate?.generation_anchor_sha256 || "", /^[0-9a-f]{64}$/);
});

test("finish overwrites the durable active-generation record with an empty snapshot", async () => {
  const store = createSessionStore();
  const registry = createCurrentEntryRegistry({ sessionStore: store });
  await registry.observeStart(req("r1", 9));
  assert.equal(store.hasEntries(), true);

  assert.equal(await registry.observeFinish({ requestId: "r1" }), true);
  assert.equal(store.hasEntries(), true);
  assert.equal(await createCurrentEntryRegistry({ sessionStore: store }).resolveCurrent(), null);
});

test("TTL pruning overwrites the durable active-generation record with an empty snapshot", async () => {
  const time = clock();
  const store = createSessionStore();
  const registry = createCurrentEntryRegistry({ clock: time.now, maxAgeMs: 1000, sessionStore: store });
  await registry.observeStart(req("r1", 9));
  time.add(1001);

  assert.equal(await registry.resolveCurrent(), null);
  assert.equal(store.hasEntries(), true);
});

test("session-store write failures reject mutations and make resolution fail closed", async () => {
  const store = {
    async get() { return {}; },
    async set() { throw new Error("quota unavailable"); },
    async remove() { throw new Error("quota unavailable"); }
  };
  const registry = createCurrentEntryRegistry({ sessionStore: store });

  await assert.rejects(registry.observeStart(req("r1", 9)), /session store/i);
  await assert.rejects(registry.resolveCurrent(), /session store/i);
});

test("corrupt session-store data makes the registry fail closed", async () => {
  const store = {
    async get(key) { return { [key]: "not-a-registry-record" }; },
    async set() {},
    async remove() {}
  };
  const registry = createCurrentEntryRegistry({ sessionStore: store });

  await assert.rejects(registry.resolveCurrent(), /session store/i);
});

test("resolve serializes after queued mutations", async () => {
  const store = createSessionStore();
  const registry = createCurrentEntryRegistry({ sessionStore: store });
  const started = registry.observeStart(req("r1", 9));
  const resolved = registry.resolveCurrent();

  await started;
  assert.equal((await resolved)?.tab_id, 9);
  await registry.observeFinish({ requestId: "r1" });
  assert.equal(await registry.resolveCurrent(), null);
});

test("unawaited start and finish persist active then empty snapshots in order", async () => {
  const writes = [];
  const releases = [];
  const store = {
    async get() { return {}; },
    async set(entries) {
      writes.push(entries);
      await new Promise((resolve) => releases.push(resolve));
    },
    async remove() { throw new Error("remove must not be used"); }
  };
  const registry = createCurrentEntryRegistry({ sessionStore: store });
  const started = registry.observeStart(req("r1", 9));
  const finished = registry.observeFinish({ requestId: "r1" });
  const resolved = registry.resolveCurrent();

  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(writes.length, 1);
  releases.shift()();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(writes.length, 2);
  releases.shift()();
  await started;
  await finished;
  assert.equal(await resolved, null);
});

test("registry requires chrome.storage.session unless a store is explicitly supplied", () => {
  assert.throws(() => createCurrentEntryRegistry({ sessionStore: undefined }), /required/);
});

test("registry requires a positive safe max age", () => {
  for (const maxAgeMs of [0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(
      () => createCurrentEntryRegistry({ maxAgeMs, sessionStore: createSessionStore() }),
      /max age/i
    );
  }
});

test("finish writes an empty snapshot without calling session-store remove", async () => {
  const store = createSessionStore();
  store.remove = async function () { throw new Error("remove must not be used"); };
  const registry = createCurrentEntryRegistry({ sessionStore: store });
  await registry.observeStart(req("r1", 9));

  assert.equal(await registry.observeFinish({ requestId: "r1" }), true);
  assert.equal(await createCurrentEntryRegistry({ sessionStore: store }).resolveCurrent(), null);
});

test("a failed empty snapshot write rejects without claiming durable deletion", async () => {
  const store = createSessionStore();
  const registry = createCurrentEntryRegistry({ sessionStore: store });
  await registry.observeStart(req("r1", 9));
  store.set = async function () { throw new Error("quota unavailable"); };

  await assert.rejects(registry.observeFinish({ requestId: "r1" }), /session store/i);
  await assert.rejects(registry.resolveCurrent(), /session store/i);
  assert.equal((await createCurrentEntryRegistry({ sessionStore: store }).resolveCurrent())?.tab_id, 9);
});

test("same-tab duplicate request does not extend its TTL", async () => {
  const time = clock();
  const registry = createRegistry({ clock: time.now, maxAgeMs: 1000 });
  await registry.observeStart(req("r1", 9));
  time.add(500);
  await registry.observeStart(req("r1", 9));
  time.add(500);

  assert.equal(await registry.resolveCurrent(), null);
});

test("same request id observed in another tab makes the registry fail closed", async () => {
  const registry = createRegistry();
  await registry.observeStart(req("r1", 9));

  await assert.rejects(registry.observeStart(req("r1", 10)), /request.*tab/i);
  await assert.rejects(registry.resolveCurrent(), /request.*tab/i);
});

test("rebuilt registry keeps multiple active tabs ambiguous", async () => {
  const store = createSessionStore();
  const first = createCurrentEntryRegistry({ sessionStore: store });
  await first.observeStart(req("r1", 9));
  await first.observeStart(req("r2", 10));

  await assert.rejects(createCurrentEntryRegistry({ sessionStore: store }).resolveCurrent(), /AMBIGUOUS/);
});

test("corrupt duplicate request ids and invalid stored timestamps fail closed", async () => {
  for (const active of [
    [
      { request_id: "r1", tab_id: 9, started_at_ms: 1800000000000 },
      { request_id: "r1", tab_id: 9, started_at_ms: 1800000000001 }
    ],
    ...[0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1].map((started_at_ms) => [
      { request_id: "r1", tab_id: 9, started_at_ms }
    ]),
    [{ request_id: "r1", tab_id: 9, started_at_ms: 1800000000000, unexpected: true }]
  ]) {
    const store = {
      async get(key) { return { [key]: { version: 1, active } }; },
      async set() {}
    };
    await assert.rejects(createCurrentEntryRegistry({ sessionStore: store }).resolveCurrent(), /session store/i);
  }
});

test("hydration rejects records beyond the allowed future clock skew", async () => {
  const time = clock();
  const store = {
    async get(key) {
      return {
        [key]: {
          version: 1,
          active: [{ request_id: "r1", tab_id: 9, started_at_ms: time.now() + 5001 }]
        }
      };
    },
    async set() {}
  };

  await assert.rejects(createCurrentEntryRegistry({ clock: time.now, sessionStore: store }).resolveCurrent(), /session store/i);
});

test("clock rollback beyond future skew makes an active generation fail closed", async () => {
  const time = clock();
  const registry = createRegistry({ clock: time.now });
  await registry.observeStart(req("r1", 9));
  time.add(-5001);

  await assert.rejects(registry.resolveCurrent(), /timestamp/i);
});

test("invalid clock timestamps fail closed without being reported as storage corruption", async () => {
  const registry = createCurrentEntryRegistry({
    clock: () => 0,
    sessionStore: createSessionStore()
  });

  await assert.rejects(registry.resolveCurrent(), /clock timestamp/i);
});

test("hydration rejects oversized request ids and too many active records", async () => {
  const time = clock();
  for (const active of [
    [{ request_id: "r".repeat(257), tab_id: 9, started_at_ms: time.now() }],
    Array.from({ length: 65 }, (_, index) => ({
      request_id: `r${index}`,
      tab_id: 9,
      started_at_ms: time.now()
    }))
  ]) {
    const store = {
      async get(key) { return { [key]: { version: 1, active } }; },
      async set() {}
    };
    await assert.rejects(createCurrentEntryRegistry({ clock: time.now, sessionStore: store }).resolveCurrent(), /session store/i);
  }
});

test("hydration rejects stringified stored tab and timestamp fields", async () => {
  const time = clock();
  const store = {
    async get(key) {
      return {
        [key]: {
          version: 1,
          active: [{ request_id: "r1", tab_id: "9", started_at_ms: String(time.now()) }]
        }
      };
    },
    async set() {}
  };

  await assert.rejects(createCurrentEntryRegistry({ clock: time.now, sessionStore: store }).resolveCurrent(), /session store/i);
});

import { selectCurrentEntryTarget } from "./extension/current-entry-registry.js";

test("current-entry target selection binds generation tab to exactly one secure ChatGPT page", () => {
  const candidate = { tab_id: 9 };
  const result = selectCurrentEntryTarget([
    { id: "a", tabId: 8, type: "page", url: "https://chatgpt.com/c/other" },
    { id: "b", tabId: 9, type: "page", url: "https://chatgpt.com/c/current" },
    { id: "c", tabId: 9, type: "service_worker", url: "https://chatgpt.com/sw.js" }
  ], candidate);
  assert.equal(result.id, "b");
});

test("current-entry target selection rejects missing or ambiguous target", () => {
  assert.throws(
    () => selectCurrentEntryTarget([{ id: "x", tabId: 9, type: "page", url: "http://chatgpt.com/c/x" }], { tab_id: 9 }),
    /UNAVAILABLE/
  );
  assert.throws(
    () => selectCurrentEntryTarget([
      { id: "a", tabId: 9, type: "page", url: "https://chatgpt.com/c/a" },
      { id: "b", tabId: 9, type: "page", url: "https://chatgpt.com/c/b" }
    ], { tab_id: 9 }),
    /AMBIGUOUS/
  );
});

const CONVERSATION = "6ab93a76-4e6c-83e9-95ae-71d28091ef08";
const OTHER_CONVERSATION = "6a9e4158-cc8c-83ea-9763-05a7fd51f597";

function chatgptPage(tabId, conversation = CONVERSATION) {
  return {
    id: "page-" + tabId,
    tabId,
    type: "page",
    url: "https://chatgpt.com/g/g-p-6ab93a6fa99c8191b561d11798d0bf82-local-agent-bridge/c/" + conversation
  };
}

function swGeneration(overrides = {}) {
  return {
    requestId: "sw-r1",
    tabId: -1,
    method: "POST",
    url: "https://chatgpt.com/backend-api/f/conversation",
    initiator: "https://chatgpt.com",
    requestHeaders: [{
      name: "Referer",
      value: "https://chatgpt.com/g/g-p-6ab93a6fa99c8191b561d11798d0bf82-local-agent-bridge/c/" + CONVERSATION
    }],
    ...overrides
  };
}

test("service worker generation with chatgpt initiator and referer records the unique tab", async () => {
  const registry = createRegistry({
    listChatGptPages: async () => [chatgptPage(11)]
  });
  assert.equal(await registry.observeStart(swGeneration()), true);
  assert.equal((await registry.resolveCurrent()).tab_id, 11);
});

test("service worker generation can observeStart before the streaming request finishes", async () => {
  const registry = createRegistry({
    listChatGptPages: async () => [chatgptPage(11)]
  });
  assert.equal(await registry.observeStart(swGeneration()), true);
  assert.equal((await registry.resolveCurrent())?.tab_id, 11);
  assert.equal(await registry.observeFinish({ requestId: "sw-r1", tabId: -1 }), true);
  assert.equal(await registry.resolveCurrent(), null);
});

test("service worker generation without referer does not guess a tab", async () => {
  const registry = createRegistry({
    listChatGptPages: async () => [chatgptPage(11), chatgptPage(12, OTHER_CONVERSATION)]
  });
  assert.equal(await registry.observeStart(swGeneration({ requestHeaders: [] })), false);
  assert.equal(await registry.observeStart({
    ...swGeneration({ requestHeaders: [] }),
    conversation_id: CONVERSATION
  }), false);
  assert.equal(await registry.resolveCurrent(), null);
});

test("service worker generation binds the referer conversation among other chatgpt tabs", async () => {
  const registry = createRegistry({
    listChatGptPages: async () => [
      chatgptPage(8, OTHER_CONVERSATION),
      chatgptPage(11),
      { id: "sw", tabId: -1, type: "service_worker", url: "https://chatgpt.com/sw.js" }
    ]
  });
  assert.equal(await registry.observeStart(swGeneration()), true);
  assert.equal((await registry.resolveCurrent()).tab_id, 11);
});

test("service worker generation fails closed when multiple tabs share the same conversation id", async () => {
  const registry = createRegistry({
    listChatGptPages: async () => [chatgptPage(11), chatgptPage(14)]
  });
  await assert.rejects(
    registry.observeStart(swGeneration()),
    /HOST_CURRENT_ENTRY_AMBIGUOUS/
  );
  assert.equal(await registry.resolveCurrent(), null);
});

test("service worker generation with conflicting conversation evidence fails closed", async () => {
  const registry = createRegistry({
    listChatGptPages: async () => [chatgptPage(11), chatgptPage(12, OTHER_CONVERSATION)]
  });
  await assert.rejects(registry.observeStart(swGeneration({
    documentUrl: "https://chatgpt.com/c/" + OTHER_CONVERSATION
  })), /AMBIGUOUS/);
  await assert.rejects(registry.observeStart(swGeneration({
    requestHeaders: [{
      name: "Referer",
      value: "https://chatgpt.com/c/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    }]
  })), /UNAVAILABLE/);
  assert.equal(await registry.resolveCurrent(), null);
});
