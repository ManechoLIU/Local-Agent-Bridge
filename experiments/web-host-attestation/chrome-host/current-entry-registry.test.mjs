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

test("registry recognizes only ChatGPT generation requests", () => {
  assert.equal(isChatGptGenerationRequest(req("r1", 7)), true);
  assert.equal(isChatGptGenerationRequest(req("r2", 7, "https://chatgpt.com/backend-api/conversation")), true);
  assert.equal(isChatGptGenerationRequest(req("r3", 7, "https://chatgpt.com/backend-api/conversations", "GET")), false);
  assert.equal(isChatGptGenerationRequest(req("r4", -1)), false);
  assert.equal(isChatGptGenerationRequest(req("r5", 7, "https://example.com/backend-api/f/conversation")), false);
});

test("unique active generation resolves exact tab and opaque generation anchor", async () => {
  const time = clock();
  const registry = createCurrentEntryRegistry({ clock: time.now });
  assert.equal(registry.observeStart(req("r1", 11)), true);
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
  const registry = createCurrentEntryRegistry();
  registry.observeStart(req("r1", 9));
  registry.observeStart(req("r2", 9, "https://chatgpt.com/backend-api/conversation"));
  const candidate = await registry.resolveCurrent();
  assert.equal(candidate.tab_id, 9);
  const reordered = createCurrentEntryRegistry();
  reordered.observeStart(req("r2", 9, "https://chatgpt.com/backend-api/conversation"));
  reordered.observeStart(req("r1", 9));
  assert.equal(candidate.generation_anchor_sha256, (await reordered.resolveCurrent()).generation_anchor_sha256);
});

test("concurrent generations in different tabs fail closed", async () => {
  const registry = createCurrentEntryRegistry();
  registry.observeStart(req("r1", 9));
  registry.observeStart(req("r2", 10));
  await assert.rejects(registry.resolveCurrent(), /AMBIGUOUS/);
});

test("stale unfinished request is pruned instead of becoming current entry", async () => {
  const time = clock();
  const registry = createCurrentEntryRegistry({ clock: time.now, maxAgeMs: 1000 });
  registry.observeStart(req("r1", 9));
  time.add(1001);
  assert.equal(await registry.resolveCurrent(), null);
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
