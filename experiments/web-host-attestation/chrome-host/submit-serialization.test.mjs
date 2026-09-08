import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import {
  pagePrepareSubmitFunction,
  pageConfirmSubmitFunction
} from "./extension/submit-page.js";

const CONVERSATION = "6a9e4158-cc8c-83ea-9763-05a7fd51f597";

function contextFor(composer) {
  const button = {
    disabled: false,
    getAttribute() { return null; },
    getBoundingClientRect() {
      return { left: 10, top: 20, width: 30, height: 40 };
    }
  };
  const context = vm.createContext({
    URL,
    Date,
    Promise,
    Number,
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
        return null;
      },
      querySelectorAll() { return []; },
      execCommand(command, _ui, text) {
        if (command !== "insertText") return false;
        composer.textContent = text;
        composer.innerText = text;
        return true;
      }
    }
  });
  context.globalThis = context;
  return context;
}

test("prepare and confirm stay self-contained after chrome.scripting serialization", async function () {
  const composer = {
    textContent: "",
    innerText: "",
    focus() {},
    dispatchEvent() {}
  };
  const context = contextFor(composer);
  const prepare = vm.runInContext("(" + pagePrepareSubmitFunction.toString() + ")", context);
  const prepared = await prepare("resume", CONVERSATION);
  assert.equal(prepared.ready_to_dispatch, true);
  assert.deepEqual(JSON.parse(JSON.stringify(prepared.click_point)), { x: 25, y: 40 });

  composer.textContent = "";
  composer.innerText = "";
  const confirm = vm.runInContext("(" + pageConfirmSubmitFunction.toString() + ")", context);
  const confirmed = await confirm("resume", CONVERSATION, 250);
  assert.equal(confirmed.submit_confirmed, true);
});
