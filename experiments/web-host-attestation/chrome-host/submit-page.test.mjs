import test from "node:test";
import assert from "node:assert/strict";
import { pageSubmitFunction } from "./extension/submit-page.js";

const CONVERSATION = "6a9e4158-cc8c-83ea-9763-05a7fd51f597";
const OTHER = "6a9e41a2-266c-83ea-9104-e768ed4ad3f2";

function withPage(t, options) {
  const composer = {
    textContent: options.initialText || "",
    innerText: options.initialText || "",
    focused: false,
    focus() { this.focused = true; },
    dispatchEvent() {}
  };
  const button = options.button === false ? null : {
    disabled: Boolean(options.disabled),
    getAttribute() { return null; },
    click() {
      if (options.clickThrows) throw new Error("click failed");
      if (options.clearOnClick !== false) {
        composer.textContent = "";
        composer.innerText = "";
      }
    }
  };
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

  return { composer: composer };
}

test("page submit refuses wrong conversation before touching composer", async function (t) {
  const page = withPage(t, { conversation: OTHER });
  const result = await pageSubmitFunction("resume", CONVERSATION);
  assert.equal(result.status, "wrong_conversation");
  assert.equal(result.dispatch_attempted, false);
  assert.equal(page.composer.focused, false);
});

test("page submit never overwrites an existing user draft", async function (t) {
  const page = withPage(t, { initialText: "user draft" });
  const result = await pageSubmitFunction("resume", CONVERSATION);
  assert.equal(result.status, "composer_not_empty");
  assert.equal(result.dispatch_attempted, false);
  assert.equal(page.composer.innerText, "user draft");
});

test("page submit confirms only after click and composer clear", async function (t) {
  withPage(t, {});
  const result = await pageSubmitFunction("resume", CONVERSATION);
  assert.deepEqual(result, {
    dispatch_attempted: true,
    submit_confirmed: true,
    retryable: false,
    status: "submit_confirmed"
  });
});

test("page submit is result unknown after click without confirmation", async function (t) {
  withPage(t, { clearOnClick: false });
  const result = await pageSubmitFunction("resume", CONVERSATION);
  assert.equal(result.dispatch_attempted, true);
  assert.equal(result.submit_confirmed, false);
  assert.equal(result.retryable, false);
  assert.equal(result.status, "result_unknown");
});

test("missing send button is retryable only when own payload is removed", async function (t) {
  const page = withPage(t, { button: false });
  const result = await pageSubmitFunction("resume", CONVERSATION);
  assert.equal(result.dispatch_attempted, false);
  assert.equal(result.retryable, true);
  assert.equal(result.status, "send_unavailable");
  assert.equal(page.composer.innerText, "");
});
