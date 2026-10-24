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
  const modernComposers = options.modernComposer
    ? [composer, ...(options.ambiguousComposer ? [{ ...composer }] : [])] : [];
  for (const node of modernComposers) {
    node.getBoundingClientRect = () => ({ width: 300, height: 48 });
  }
  const sendRect = options.sendRect || { left: 100, top: 200, width: 40, height: 20 };
  const sendTestId = options.sendTestId !== false;
  const stopTestId = options.stopTestId !== false;
  const sendButton = options.sendButton === false ? null : {
    disabled: Boolean(options.sendDisabled),
    getAttribute(name) {
      if (name === "data-testid") return sendTestId ? "send-button" : null;
      if (name === "aria-disabled") return options.sendAriaDisabled ? "true" : null;
      if (name === "aria-label") return options.sendAriaLabel || "Send";
      return null;
    },
    getBoundingClientRect() { return { ...sendRect }; }
  };
  const stopButton = options.controllerActive ? {
    disabled: false,
    getAttribute(name) {
      if (name === "data-testid") return stopTestId ? "stop-button" : null;
      if (name === "aria-label") return options.stopAriaLabel || "Stop answering";
      return null;
    }
  } : null;
  const userMessages = (options.userMessages || []).map(text => typeof text === "object" ? text : ({
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
      if (selector === '#prompt-textarea[contenteditable="true"]') {
        return options.composer === false || options.modernComposer ? null : composer;
      }
      if (selector === 'button[data-testid="send-button"]') return sendTestId ? sendButton : null;
      if (selector === 'button[data-testid="stop-button"]') return stopTestId ? stopButton : null;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === '[data-message-author-role="user"]') return options.modernMessages ? [] : userMessages;
      if (selector === '[data-user-message-bubble="true"]') return options.modernMessages ? userMessages : [];
      if (selector === 'form[data-chatgpt-composer] [data-composer-markdown][contenteditable="true"][role="textbox"]') return modernComposers;
      if (selector === 'form[data-chatgpt-composer] button') return [sendButton, stopButton].filter(Boolean);
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

function bubbleWithContent(text, chromeLabel) {
  const body = { innerText: text, textContent: text };
  return {
    innerText: text + "\n" + chromeLabel,
    querySelector(selector) { return selector === "[data-search-result-target]" ? body : null; }
  };
}

test("message body ending in 展开 must never be shortened into a different exact payload", async t => {
  withPage(t, { modernComposer: true, modernMessages: true, userMessages: ["resume\n展开"] });
  assert.equal((await op("confirm", { payload: "resume", baseline_user_message_count: 0, timeout_ms: 250 })).submit_confirmed, false);
  const hash = Buffer.from(await webcrypto.subtle.digest("SHA-256", new TextEncoder().encode("resume"))).toString("hex");
  assert.equal((await op("reconcile", { expected_payload_sha256: hash, baseline_user_message_count: 0 })).exact_user_message_present, false);
});

test("a legitimate payload ending in 展开 remains an exact user message", async t => {
  withPage(t, { modernComposer: true, modernMessages: true, userMessages: ["resume\n展开"] });
  assert.equal((await op("confirm", { payload: "resume\n展开", baseline_user_message_count: 0, timeout_ms: 250 })).submit_confirmed, true);
});

test("current ChatGPT composer supports prepare, resolve and exact message confirmation", async t => {
  const page = withPage(t, { modernComposer: true, modernMessages: true, userMessages: ["old"] });
  const prepared = await op("prepare", { payload: "resume" });
  assert.equal(prepared.prepared, true);
  assert.equal(prepared.baseline_user_message_count, 1);
  assert.equal((await op("resolve_send", { payload: "resume" })).resolved, true);
  page.userMessages.push({ innerText: "resume", textContent: "resume" });
  page.composer.innerText = page.composer.textContent = "";
  assert.equal((await op("confirm", { payload: "resume", baseline_user_message_count: 1 })).submit_confirmed, true);
});

test("current localized Stop control prevents writing into an active Controller", async t => {
  const page = withPage(t, { modernComposer: true, controllerActive: true, stopTestId: false, stopAriaLabel: "停止" });
  const result = await op("prepare", { payload: "resume" });
  assert.equal(result.status, "controller_active");
  assert.equal(page.composer.innerText, "");
  assert.equal(page.composer.focused, false);
});

test("current composer refuses ambiguous editors and preserves existing user drafts", async t => {
  const page = withPage(t, { modernComposer: true, ambiguousComposer: true, initialText: "user draft" });
  assert.equal((await op("prepare", { payload: "resume" })).prepared, false);
  assert.equal(page.composer.innerText, "user draft");
  assert.equal(page.composer.focused, false);
});

test("current composer supports exact owned payload rollback without clearing another draft", async t => {
  const page = withPage(t, { modernComposer: true, initialText: "user draft" });
  const hash = Buffer.from(await webcrypto.subtle.digest("SHA-256", new TextEncoder().encode("resume"))).toString("hex");
  assert.equal((await op("prepare_rollback", { expected_payload_sha256: hash })).safe_to_clear, false);
  page.composer.innerText = page.composer.textContent = "resume";
  assert.equal((await op("prepare_rollback", { expected_payload_sha256: hash })).safe_to_clear, true);
});

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
  assert.equal(result.ready_to_dispatch, true);
  assert.equal(result.dispatch_attempted, false);
  assert.equal(result.status, "prepared");
  assert.equal(result.baseline_user_message_count, 1);
  assert.equal(result.initial_send_control.selector_kind, "testid_send_button");
  assert.equal(page.composer.innerText, "resume");
  assert.equal(page.composer.focused, false);
});

test("prepare fails closed when composer payload already exists as an exact user bubble", async t => {
  const page = withPage(t, { initialText: "resume", userMessages: ["old", "resume"] });
  const result = await op("prepare", { payload: "resume" });
  assert.equal(result.prepared, false);
  assert.equal(result.ready_to_dispatch, false);
  assert.equal(result.dispatch_attempted, false);
  assert.equal(result.retryable, false);
  assert.equal(result.status, "payload_already_submitted");
  assert.equal(result.initial_send_control, null);
  assert.equal(page.composer.innerText, "resume");
  assert.equal(page.composer.focused, false);
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

test("resolve_send matches Chinese aria-label 发送提示词", async t => {
  withPage(t, {
    initialText: "resume",
    sendTestId: false,
    sendAriaLabel: "发送提示词"
  });
  const resolved = await op("resolve_send", { payload: "resume" });
  assert.equal(resolved.resolved, true);
  assert.equal(resolved.send_control.selector_kind, "aria_send");
});

test("prepare detects Chinese aria-label 停止回答 as controller_active", async t => {
  const page = withPage(t, {
    initialText: "resume",
    controllerActive: true,
    stopTestId: false,
    stopAriaLabel: "停止回答"
  });
  const result = await op("prepare", { payload: "resume" });
  assert.equal(result.status, "controller_active");
  assert.equal(result.prepared, false);
  assert.equal(result.retryable, true);
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

test("confirm sees exact last visible user bubble when ChatGPT virtualizes history below baseline", async t => {
  const payload = "LAB-HOST-01 live reentry CONFIRMED probe. Do not treat as user chat. Reply with one word: ACK.";
  withPage(t, { initialText: "", userMessages: ["older visible", payload] });
  const confirmed = await op("confirm", {
    payload,
    baseline_user_message_count: 50,
    timeout_ms: 250
  });
  assert.equal(confirmed.submit_confirmed, true);
  assert.equal(confirmed.exact_new_user_message, true);
  assert.equal(confirmed.status, "submit_confirmed");
});

test("confirm does not treat an earlier identical visible bubble as the new submit", async t => {
  withPage(t, { initialText: "", userMessages: ["resume", "later user turn"] });
  const unknown = await op("confirm", {
    payload: "resume",
    baseline_user_message_count: 50,
    timeout_ms: 250
  });
  assert.equal(unknown.submit_confirmed, false);
  assert.equal(unknown.exact_new_user_message, false);
  assert.equal(unknown.status, "result_unknown");
});

test("confirm matches exact payload when ChatGPT appends trailing 展开 chrome", async t => {
  const payload = "Wake generation=23. Do not treat as user chat.";
  withPage(t, { initialText: "", userMessages: ["old", bubbleWithContent(payload, "展开")] });
  const confirmed = await op("confirm", {
    payload,
    baseline_user_message_count: 1,
    timeout_ms: 250
  });
  assert.equal(confirmed.submit_confirmed, true);
  assert.equal(confirmed.exact_new_user_message, true);
  assert.equal(confirmed.status, "submit_confirmed");
});

test("confirm does not strip 展开 inside payload body", async t => {
  const payload = "Wake 展开 generation=23. Keep this body.";
  withPage(t, { initialText: "", userMessages: ["old", bubbleWithContent(payload, "展开")] });
  const confirmed = await op("confirm", {
    payload,
    baseline_user_message_count: 1,
    timeout_ms: 250
  });
  assert.equal(confirmed.submit_confirmed, true);
  assert.equal(confirmed.exact_new_user_message, true);

  withPage(t, { initialText: "", userMessages: ["old", "unrelated 展开 body\n展开"] });
  const unknown = await op("confirm", {
    payload,
    baseline_user_message_count: 1,
    timeout_ms: 250
  });
  assert.equal(unknown.submit_confirmed, false);
  assert.equal(unknown.exact_new_user_message, false);
  assert.equal(unknown.status, "result_unknown");
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

test("reconcile proves exact composer and user-message state from payload hash only", async t => {
  const page = withPage(t, { initialText: "resume", userMessages: ["old"] });
  const digest = Buffer.from(await webcrypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode("resume")
  )).toString("hex");
  const result = await op("reconcile", {
    expected_payload_sha256: digest,
    baseline_user_message_count: 1
  });
  assert.equal(result.status, "observed");
  assert.equal(result.conversation_matches, true);
  assert.equal(result.composer_exact_payload, true);
  assert.equal(result.exact_user_message_present, false);
  assert.equal(page.composer.innerText, "resume");
});

test("reconcile ignores identical user messages that predate the last visible bubble", async t => {
  withPage(t, { initialText: "resume", userMessages: ["resume", "later user turn"] });
  const digest = Buffer.from(await webcrypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode("resume")
  )).toString("hex");
  const result = await op("reconcile", {
    expected_payload_sha256: digest,
    baseline_user_message_count: 50
  });
  assert.equal(result.status, "observed");
  assert.equal(result.conversation_matches, true);
  assert.equal(result.composer_exact_payload, true);
  assert.equal(result.exact_user_message_present, false);
});

test("reconcile sees exact last visible user bubble when ChatGPT virtualizes history below baseline", async t => {
  const payload = "LAB-HOST-01 live reentry CONFIRMED probe. Do not treat as user chat. Reply with one word: ACK.";
  withPage(t, { initialText: "", userMessages: ["older visible", payload] });
  const digest = Buffer.from(await webcrypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(payload)
  )).toString("hex");
  const result = await op("reconcile", {
    expected_payload_sha256: digest,
    baseline_user_message_count: 50
  });
  assert.equal(result.status, "observed");
  assert.equal(result.conversation_matches, true);
  assert.equal(result.exact_user_message_present, true);
});

test("reconcile hashes chrome-stripped user bubble against exact payload", async t => {
  const payload = "Wake generation=23. Do not treat as user chat.";
  withPage(t, { initialText: "", userMessages: ["old", bubbleWithContent(payload, "展开")] });
  const digest = Buffer.from(await webcrypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(payload)
  )).toString("hex");
  const result = await op("reconcile", {
    expected_payload_sha256: digest,
    baseline_user_message_count: 1
  });
  assert.equal(result.status, "observed");
  assert.equal(result.conversation_matches, true);
  assert.equal(result.exact_user_message_present, true);
});

test("reconcile missing or invalid baseline stays unresolved without manufacturing delivery evidence", async t => {
  withPage(t, { initialText: "resume", userMessages: ["old"] });
  const digest = Buffer.from(await webcrypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode("resume")
  )).toString("hex");
  const missing = await op("reconcile", { expected_payload_sha256: digest });
  assert.equal(missing.status, "invalid_baseline_user_message_count");
  assert.equal(missing.exact_user_message_present, null);
  assert.equal(missing.composer_exact_payload, null);
  assert.notEqual(missing.status, "confirmed_not_delivered");

  const invalid = await op("reconcile", {
    expected_payload_sha256: digest,
    baseline_user_message_count: -1
  });
  assert.equal(invalid.status, "invalid_baseline_user_message_count");
  assert.equal(invalid.exact_user_message_present, null);
});

test("reconcile unknown conversation evidence stays null instead of false", async t => {
  withPage(t, { initialText: "resume" });
  const invalidHash = await op("reconcile", { expected_payload_sha256: "nope" });
  assert.equal(invalidHash.status, "invalid_payload_hash");
  assert.equal(invalidHash.conversation_matches, null);
  assert.equal(invalidHash.composer_exact_payload, null);
  assert.equal(invalidHash.exact_user_message_present, null);

  globalThis.location = { href: "https://example.com/" };
  const digest = Buffer.from(await webcrypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode("resume")
  )).toString("hex");
  const unreadable = await op("reconcile", {
    expected_payload_sha256: digest,
    baseline_user_message_count: 0
  });
  assert.equal(unreadable.status, "wrong_conversation");
  assert.equal(unreadable.conversation_matches, null);
  assert.equal(unreadable.composer_exact_payload, null);
  assert.equal(unreadable.exact_user_message_present, null);
});

test("reconcile missing composer does not manufacture exact-payload false", async t => {
  withPage(t, { initialText: "resume", composer: false, userMessages: ["old"] });
  const digest = Buffer.from(await webcrypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode("resume")
  )).toString("hex");
  const result = await op("reconcile", {
    expected_payload_sha256: digest,
    baseline_user_message_count: 1
  });
  assert.equal(result.status, "composer_unavailable");
  assert.equal(result.conversation_matches, true);
  assert.equal(result.composer_exact_payload, null);
  assert.equal(result.composer_empty, null);
  assert.notEqual(result.composer_exact_payload, false);
  assert.equal(result.exact_user_message_present, false);
});

test("verify_empty does not mutate composer", async t => {
  const page = withPage(t, { initialText: "user content" });
  const result = await op("verify_empty");
  assert.equal(result.composer_empty, false);
  assert.equal(page.composer.innerText, "user content");
});
