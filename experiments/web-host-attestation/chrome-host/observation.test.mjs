import test from "node:test";
import assert from "node:assert/strict";
import {
  MACHINE_SOURCE,
  observationFromDebugger,
  selectExactTarget,
  selectUniqueConversationTarget
} from "./extension/observation.js";

const CONVERSATION = "6a9e4158-cc8c-83ea-9763-05a7fd51f597";
const OTHER = "6a9e41a2-266c-83ea-9104-e768ed4ad3f2";

function target(id, conversation) {
  return {
    id: id,
    tabId: 7,
    type: "page",
    title: "correlation-only title",
    url: "https://chatgpt.com/g/g-p-local/c/" + conversation
  };
}

function frame(conversation, overrides) {
  return {
    frameTree: {
      frame: Object.assign({
        id: "top-frame",
        loaderId: "loader-current",
        securityOrigin: "https://chatgpt.com",
        url: "https://chatgpt.com/g/g-p-local/c/" + conversation
      }, overrides || {})
    }
  };
}

test("debugger observation binds exact target top frame loader origin and conversation", function () {
  const observed = observationFromDebugger({
    targetInfo: target("browser-target-7", CONVERSATION),
    frameTree: frame(CONVERSATION),
    observedAtMs: 1800000000100,
    dom: "must be ignored",
    headers: { "x-openai-session": "correlation-only" }
  });
  assert.deepEqual(observed, {
    host_type: "chatgpt_web",
    browser_target_id: "browser-target-7",
    top_frame_id: "top-frame",
    loader_id: "loader-current",
    secure_origin: "https://chatgpt.com",
    conversation_id: CONVERSATION,
    observed_at_ms: 1800000000100,
    source: MACHINE_SOURCE
  });
  assert.equal("title" in observed, false);
  assert.equal("dom" in observed, false);
  assert.equal("headers" in observed, false);
});

test("non-chatgpt origin missing loader and route mismatch fail closed", function () {
  assert.throws(function () {
    observationFromDebugger({
      targetInfo: target("target", CONVERSATION),
      frameTree: frame(CONVERSATION, { securityOrigin: "https://example.com" }),
      observedAtMs: 1
    });
  }, /secure chatgpt.com/);

  assert.throws(function () {
    observationFromDebugger({
      targetInfo: target("target", CONVERSATION),
      frameTree: frame(CONVERSATION, { loaderId: "" }),
      observedAtMs: 1
    });
  }, /loader id is required/);

  assert.throws(function () {
    observationFromDebugger({
      targetInfo: target("target", CONVERSATION),
      frameTree: frame(OTHER),
      observedAtMs: 1
    });
  }, /conversation differ/);
});

test("conversation target lookup rejects missing and ambiguous routes", function () {
  assert.equal(
    selectUniqueConversationTarget([
      target("one", CONVERSATION),
      target("other", OTHER)
    ], CONVERSATION).id,
    "one"
  );

  assert.throws(function () {
    selectUniqueConversationTarget([target("other", OTHER)], CONVERSATION);
  }, /unavailable/);

  assert.throws(function () {
    selectUniqueConversationTarget([
      target("one", CONVERSATION),
      target("two", CONVERSATION)
    ], CONVERSATION);
  }, /ambiguous/);
});

test("exact target lookup rejects replaced target and non-ChatGPT target", function () {
  assert.equal(selectExactTarget([target("one", CONVERSATION)], "one").id, "one");
  assert.throws(function () {
    selectExactTarget([target("two", CONVERSATION)], "one");
  }, /unavailable or replaced/);
  assert.throws(function () {
    selectExactTarget([{
      id: "one",
      tabId: 1,
      type: "page",
      url: "https://example.com/c/" + CONVERSATION
    }], "one");
  }, /secure chatgpt.com/);
});
