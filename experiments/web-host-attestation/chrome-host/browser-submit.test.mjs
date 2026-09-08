import test from "node:test";
import assert from "node:assert/strict";
import { submitContinuationThroughChrome } from "./extension/browser-submit.js";

const CONVERSATION = "6a9e4158-cc8c-83ea-9763-05a7fd51f597";
const OTHER = "6a9e41a2-266c-83ea-9104-e768ed4ad3f2";

function target(conversation) {
  return {
    id: "target-1",
    tabId: 9,
    type: "page",
    url: "https://chatgpt.com/g/g-p-local/c/" + conversation
  };
}

function fixture(overrides) {
  let calls = 0;
  const options = {
    chromeApi: {
      scripting: {
        async executeScript() {
          calls += 1;
          if (overrides && overrides.executeThrows) throw new Error("lost receipt");
          return [{
            frameId: 0,
            result: overrides && overrides.pageResult || {
              dispatch_attempted: true,
              submit_confirmed: true,
              retryable: false,
              status: "submit_confirmed"
            }
          }];
        }
      }
    },
    getTargets: async function () {
      return [target(overrides && overrides.targetConversation || CONVERSATION)];
    },
    observeTarget: async function () {
      return {
        browser_target_id: "target-1",
        conversation_id: overrides && overrides.observedConversation || CONVERSATION
      };
    },
    message: {
      browser_target_id: "target-1",
      conversation_id: CONVERSATION,
      payload: "resume"
    },
    receiptFactory: function () { return "receipt-1"; }
  };
  return { options: options, calls: function () { return calls; } };
}

test("Chrome submit refuses wrong conversation before script execution", async function () {
  const f = fixture({ targetConversation: OTHER });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.dispatch_attempted, false);
  assert.equal(result.status, "wrong_conversation_before_dispatch");
  assert.equal(f.calls(), 0);
});

test("Chrome submit refuses changed machine observation before script execution", async function () {
  const f = fixture({ observedConversation: OTHER });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.dispatch_attempted, false);
  assert.equal(result.status, "machine_target_changed_before_dispatch");
  assert.equal(f.calls(), 0);
});

test("lost executeScript result is result unknown and never retryable", async function () {
  const f = fixture({ executeThrows: true });
  const result = await submitContinuationThroughChrome(f.options);
  assert.deepEqual(result, {
    dispatch_attempted: true,
    submit_confirmed: false,
    retryable: false,
    status: "result_unknown",
    host_submit_receipt: "chrome_submit_receipt-1"
  });
});

test("confirmed page submit returns opaque Host submit receipt", async function () {
  const f = fixture({});
  const result = await submitContinuationThroughChrome(f.options);
  assert.deepEqual(result, {
    dispatch_attempted: true,
    submit_confirmed: true,
    retryable: false,
    status: "submit_confirmed",
    host_submit_receipt: "chrome_submit_receipt-1"
  });
});

test("confirmed pre-dispatch page failure remains safely retryable", async function () {
  const f = fixture({
    pageResult: {
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: true,
      status: "composer_unavailable"
    }
  });
  const result = await submitContinuationThroughChrome(f.options);
  assert.deepEqual(result, {
    dispatch_attempted: false,
    submit_confirmed: false,
    retryable: true,
    status: "composer_unavailable",
    host_submit_receipt: null
  });
});
