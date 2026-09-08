import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { webcrypto } from "node:crypto";
import { TextEncoder } from "node:util";
import { pageSubmitOperationFunction } from "./extension/submit-page.js";

const CONVERSATION = "6a9e4158-cc8c-83ea-9763-05a7fd51f597";

test("page submit operation stays self-contained after chrome.scripting serialization", async () => {
  const composer = {
    textContent: "",
    innerText: "",
    focus() {},
    dispatchEvent() {}
  };
  const button = {
    disabled: false,
    getAttribute(name) {
      if (name === "data-testid") return "send-button";
      if (name === "aria-label") return "Send";
      return null;
    },
    getBoundingClientRect() {
      return { left: 10, top: 20, width: 30, height: 40 };
    }
  };
  const context = vm.createContext({
    URL,
    Date,
    Promise,
    Number,
    Array,
    String,
    TextEncoder,
    Uint8Array,
    crypto: webcrypto,
    setTimeout,
    clearTimeout,
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
        if (selector === 'button[data-testid="stop-button"]') return null;
        return null;
      },
      querySelectorAll(selector) {
        if (selector === '[data-message-author-role="user"]') return [];
        if (selector === "button,[role=button]" || selector === "button") return [button];
        return [];
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
  const injected = vm.runInContext("(" + pageSubmitOperationFunction.toString() + ")", context);
  const result = await injected("prepare", {
    payload: "resume",
    expected_conversation_id: CONVERSATION
  });
  assert.equal(result.prepared, true);
  assert.deepEqual(
    JSON.parse(JSON.stringify(result.initial_send_control)),
    {
      resolved: true,
      selector_kind: "testid_send_button",
      x: 25,
      y: 40,
      width: 30,
      height: 40
    }
  );
});
