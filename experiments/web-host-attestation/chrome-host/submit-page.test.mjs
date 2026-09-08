import test from "node:test";
import assert from "node:assert/strict";
import {
  pagePrepareSubmitFunction,
  pageConfirmSubmitFunction
} from "./extension/submit-page.js";

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
  let syntheticClicks = 0;
  const button = options.button === false ? null : {
    disabled: Boolean(options.disabled),
    getAttribute(name) {
      if (name === "aria-disabled") return options.ariaDisabled ? "true" : null;
      return null;
    },
    getBoundingClientRect() {
      return options.rect || { left: 100, top: 200, width: 40, height: 20 };
    },
    click() { syntheticClicks += 1; }
  };
  const userMessages = (options.userMessages || []).map(function (text) {
    return { innerText: text, textContent: text };
  });

  const oldDocument = globalThis.document;
  const oldLocation = globalThis.location;
  const oldInputEvent = globalThis.InputEvent;

  globalThis.location = {
    href: "https://chatgpt.com/g/g-p-local/c/" + (options.conversation || CONVERSATION)
  };
  globalThis.InputEvent = class {
    constructor(type) { this.type = type; }
  };
  globalThis.document = {
    querySelector(selector) {
      if (selector === '#prompt-textarea[contenteditable="true"]') return composer;
      if (selector === 'button[data-testid="send-button"]') return button;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === '[data-message-author-role="user"]') return userMessages;
      return [];
    },
    execCommand(command, _ui, text) {
      if (command !== "insertText") return false;
      composer.textContent = text;
      composer.innerText = text;
      return true;
    }
  };

  t.after(function () {
    if (oldDocument === undefined) delete globalThis.document;
    else globalThis.document = oldDocument;
    if (oldLocation === undefined) delete globalThis.location;
    else globalThis.location = oldLocation;
    if (oldInputEvent === undefined) delete globalThis.InputEvent;
    else globalThis.InputEvent = oldInputEvent;
  });

  return {
    composer,
    userMessages,
    syntheticClicks: function () { return syntheticClicks; }
  };
}

test("prepare refuses wrong conversation before touching composer", async function (t) {
  const page = withPage(t, { conversation: OTHER });
  const result = await pagePrepareSubmitFunction("resume", CONVERSATION);
  assert.equal(result.status, "wrong_conversation");
  assert.equal(result.ready_to_dispatch, false);
  assert.equal(page.composer.focused, false);
});

test("prepare never overwrites an existing user draft", async function (t) {
  const page = withPage(t, { initialText: "user draft" });
  const result = await pagePrepareSubmitFunction("resume", CONVERSATION);
  assert.equal(result.status, "composer_not_empty");
  assert.equal(result.ready_to_dispatch, false);
  assert.equal(page.composer.innerText, "user draft");
});

test("prepare inserts payload and returns trusted click coordinates without synthetic click", async function (t) {
  const page = withPage(t);
  const result = await pagePrepareSubmitFunction("resume", CONVERSATION);
  assert.deepEqual(result, {
    ready_to_dispatch: true,
    dispatch_attempted: false,
    submit_confirmed: false,
    retryable: false,
    status: "ready_to_dispatch",
    click_point: { x: 120, y: 210 }
  });
  assert.equal(page.composer.innerText, "resume");
  assert.equal(page.syntheticClicks(), 0);
});

test("prepare clears its own payload when send button is unavailable", async function (t) {
  const page = withPage(t, { button: false });
  const result = await pagePrepareSubmitFunction("resume", CONVERSATION);
  assert.equal(result.ready_to_dispatch, false);
  assert.equal(result.retryable, true);
  assert.equal(result.status, "send_unavailable");
  assert.equal(page.composer.innerText, "");
});

test("prepare clears its own payload when send button has no clickable geometry", async function (t) {
  const page = withPage(t, { rect: { left: 0, top: 0, width: 0, height: 0 } });
  const result = await pagePrepareSubmitFunction("resume", CONVERSATION);
  assert.equal(result.ready_to_dispatch, false);
  assert.equal(result.retryable, true);
  assert.equal(result.status, "send_button_not_clickable");
  assert.equal(page.composer.innerText, "");
});

test("confirm succeeds after trusted dispatch clears composer", async function (t) {
  const page = withPage(t, { initialText: "" });
  const result = await pageConfirmSubmitFunction("resume", CONVERSATION, 250);
  assert.deepEqual(result, {
    dispatch_attempted: true,
    submit_confirmed: true,
    retryable: false,
    status: "submit_confirmed"
  });
  assert.equal(page.syntheticClicks(), 0);
});

test("confirm succeeds when exact payload is visible as a user message", async function (t) {
  withPage(t, { initialText: "resume", userMessages: ["older", "resume"] });
  const result = await pageConfirmSubmitFunction("resume", CONVERSATION, 250);
  assert.equal(result.submit_confirmed, true);
  assert.equal(result.status, "submit_confirmed");
});

test("confirm remains result unknown when payload stays in composer", async function (t) {
  withPage(t, { initialText: "resume" });
  const result = await pageConfirmSubmitFunction("resume", CONVERSATION, 250);
  assert.equal(result.dispatch_attempted, true);
  assert.equal(result.submit_confirmed, false);
  assert.equal(result.retryable, false);
  assert.equal(result.status, "result_unknown");
  assert.equal(result.composer_still_payload, true);
  assert.equal(result.exact_user_message_present, false);
});
