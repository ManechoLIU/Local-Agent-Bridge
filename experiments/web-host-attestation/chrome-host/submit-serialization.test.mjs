import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { pageSubmitFunction } from "./extension/submit-page.js";

const CONVERSATION = "6a9e4158-cc8c-83ea-9763-05a7fd51f597";

test("page submit stays self-contained after chrome.scripting serialization", async function () {
  const composer = {
    textContent: "",
    innerText: "",
    focus() {},
    dispatchEvent() {}
  };
  const button = {
    disabled: false,
    getAttribute() { return null; },
    click() {
      composer.textContent = "";
      composer.innerText = "";
    }
  };
  const context = vm.createContext({
    URL: URL,
    Date: Date,
    Promise: Promise,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    location: {
      href: "https://chatgpt.com/g/g-p-local/c/" + CONVERSATION
    },
    InputEvent: class {
      constructor(type) { this.type = type; }
    },
    document: {
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
    }
  });
  context.globalThis = context;
  const injected = vm.runInContext("(" + pageSubmitFunction.toString() + ")", context);
  const result = await injected("resume", CONVERSATION);
  assert.deepEqual(JSON.parse(JSON.stringify(result)), {
    dispatch_attempted: true,
    submit_confirmed: true,
    retryable: false,
    status: "submit_confirmed"
  });
});
