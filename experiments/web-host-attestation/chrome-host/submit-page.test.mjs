import test from "node:test";
import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { TextEncoder } from "node:util";
import { pageSubmitOperationFunction } from "./extension/submit-page.js";

const CONVERSATION = "6a9e4158-cc8c-83ea-9763-05a7fd51f597";
const OTHER = "6a9e41a2-266c-83ea-9104-e768ed4ad3f2";

function withPage(t, options = {}) {
  const composer = {
    textContent: options.initialText || "",
    innerText: options.initialText || "",
    focused: false,
    focus() { this.focused = true; },
    dispatchEvent() {}
  };
  const sendRect = options.sendRect || { left: 100, top: 200, width: 40, height: 20 };
  const sendButton = options.sendButton === false ? null : {
    disabled: Boolean(options.sendDisabled),
    getAttribute(name) {
      if (name === "data-testid") return "send-button";
      if (name === "aria-disabled") return options.sendAriaDisabled ? "true" : null;
      if (name === "aria-label") return "Send";
      return null;
    },
    getBoundingClientRect() { return { ...sendRect }; }
  };
  const stopButton = options.controllerActive ? {
    disabled: false,
    getAttribute(name) {
      if (name === "data-testid") return "stop-button";
      if (name === "aria-label") return "Stop answering";
      return null;
    }
  } : null;
  const userMessages = (options.userMessages || []).map(text => ({
    innerText: text,
    textContent: text
  }));

  const old = {
    document: globalThis.document,
    location: globalThis.location,
    InputEvent: globalThis.InputEvent,
    Event: globalThis.Event
  };

  globalThis.location = {
    href: "https://chatgpt.com/g/g-p-local/c/" + (options.conversation || CONVERSATION)
  };
  globalThis.InputEvent = class {
    constructor(type) { this.type = type; }
  };
  globalThis.document = {
    querySelector(selector) {
      if (selector === '#prompt-textarea[contenteditable="true"]') return composer;
      if (selector === 'button[data-testid="send-button"]') return sendButton;
      if (selector === 'button[data-testid="stop-button"]') return stopButton;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === '[data-message-author-role="user"]') return userMessages;
      if (selector === "button,[role=button]") {
        return [sendButton, stopButton].filter(Boolean);
      }
      if (selector === "button") return [sendButton, stopButton].filter(Boolean);
      return [];
    },
    execCommand(command, _ui, text) {
      if (command !== "insertText") return false;
      composer.textContent = text;
      composer.innerText = text;
      return true;
    }
  };

  t.after(() => {
    for (const [key, value] of Object.entries(old)) {
      if (value === undefined) delete globalThis[key];
      else globalThis[key] = value;
    }
  });

  return { composer, sendRect, userMessages };
}

function op(operation, input = {}) {
  return pageSubmitOperationFunction(operation, {
    expected_conversation_id: CONVERSATION,
    ...input
  });
}

test("prepare refuses wrong conversation before touching composer", async t => {
  const page = withPage(t, { conversation: OTHER });
  const result = await op("prepare", { payload: "resume" });
  assert.equal(result.status, "wrong_conversation");
  assert.equal(result.prepared, false);
  assert.equal(page.composer.focused, false);
});

test("prepare never overwrites an existing user draft", async t => {
  const page = withPage(t, { initialText: "user draft" });
  const result = await op("prepare", { payload: "resume" });
  assert.equal(result.status, "composer_not_empty");
  assert.equal(result.prepared, false);
  assert.equal(page.composer.innerText, "user draft");
});

test("controller active is detected before Host writes payload", async t => {
  const page = withPage(t, { controllerActive: true });
  const result = await op("prepare", { payload: "resume" });
  assert.equal(result.status, "controller_active");
  assert.equal(result.prepared, false);
  assert.equal(result.retryable, true);
  assert.equal(page.composer.innerText, "");
});

test("prepare safely reuses the exact already-prepared Runtime payload", async t => {
  const page = withPage(t, { initialText: "resume", userMessages: ["old"] });
  const result = await op("prepare", { payload: "resume" });
  assert.equal(result.prepared, true);
  assert.equal(result.dispatch_attempted, false);
  assert.equal(result.status, "prepared");
  assert.equal(result.baseline_user_message_count, 1);
  assert.equal(result.initial_send_control.selector_kind, "testid_send_button");
  assert.equal(page.composer.innerText, "resume");
});

test("exact prepared Runtime payload remains retryable while controller is active", async t => {
  const page = withPage(t, { initialText: "resume", controllerActive: true });
  const result = await op("prepare", { payload: "resume" });
  assert.equal(result.status, "controller_active");
  assert.equal(result.prepared, false);
  assert.equal(result.retryable, true);
  assert.equal(page.composer.innerText, "resume");
});

test("prepare records baseline and initial send snapshot without dispatch", async t => {
  const page = withPage(t, { userMessages: ["old"] });
  const result = await op("prepare", { payload: "resume" });
  assert.equal(result.prepared, true);
  assert.equal(result.dispatch_attempted, false);
  assert.equal(result.baseline_user_message_count, 1);
  assert.equal(result.initial_send_control.selector_kind, "testid_send_button");
  assert.deepEqual(
    { x: result.initial_send_control.x, y: result.initial_send_control.y },
    { x: 120, y: 210 }
  );
  assert.equal(page.composer.innerText, "resume");
});

test("resolve_send re-resolves current coordinates and rejects changed composer", async t => {
  const page = withPage(t, { initialText: "resume", sendRect: { left: 300, top: 400, width: 20, height: 10 } });
  const resolved = await op("resolve_send", { payload: "resume" });
  assert.equal(resolved.resolved, true);
  assert.deepEqual(
    { x: resolved.send_control.x, y: resolved.send_control.y },
    { x: 310, y: 405 }
  );
  page.composer.innerText = "user changed it";
  page.composer.textContent = "user changed it";
  const changed = await op("resolve_send", { payload: "resume" });
  assert.equal(changed.resolved, false);
  assert.equal(changed.status, "composer_changed_before_dispatch");
});

test("confirm requires exact new user message instead of composer-empty alone", async t => {
  withPage(t, { initialText: "", userMessages: ["old"] });
  const unknown = await op("confirm", {
    payload: "resume",
    baseline_user_message_count: 1,
    timeout_ms: 250
  });
  assert.equal(unknown.submit_confirmed, false);
  assert.equal(unknown.composer_empty, true);

  const page = withPage(t, { initialText: "", userMessages: ["old", "resume"] });
  const confirmed = await op("confirm", {
    payload: "resume",
    baseline_user_message_count: 1,
    timeout_ms: 250
  });
  assert.equal(confirmed.submit_confirmed, true);
  assert.equal(confirmed.exact_new_user_message, true);
  assert.equal(page.userMessages.length, 2);
});

test("prepare_rollback authorizes only exact SHA-256-owned payload", async t => {
  const page = withPage(t, { initialText: "resume" });
  const digest = Buffer.from(await webcrypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode("resume")
  )).toString("hex");
  const safe = await op("prepare_rollback", { expected_payload_sha256: digest });
  assert.equal(safe.safe_to_clear, true);
  assert.equal(page.composer.focused, true);

  const mismatch = await op("prepare_rollback", { expected_payload_sha256: "0".repeat(64) });
  assert.equal(mismatch.safe_to_clear, false);
  assert.equal(mismatch.status, "composer_hash_mismatch");
});

test("verify_empty does not mutate composer", async t => {
  const page = withPage(t, { initialText: "user content" });
  const result = await op("verify_empty");
  assert.equal(result.composer_empty, false);
  assert.equal(page.composer.innerText, "user content");
});
