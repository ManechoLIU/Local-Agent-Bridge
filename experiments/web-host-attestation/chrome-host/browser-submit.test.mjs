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

function fixture(overrides = {}) {
  const calls = [];
  let scriptIndex = 0;
  const options = {
    chromeApi: {
      scripting: {
        async executeScript(input) {
          calls.push(["script", input.func.name]);
          scriptIndex += 1;
          if (overrides.executeThrowsAt === scriptIndex) throw new Error("lost script receipt");
          if (scriptIndex === 1) {
            return [{
              frameId: 0,
              result: overrides.prepareResult || {
                ready_to_dispatch: true,
                dispatch_attempted: false,
                submit_confirmed: false,
                retryable: false,
                status: "ready_to_dispatch",
                click_point: { x: 120, y: 210 }
              }
            }];
          }
          return [{
            frameId: 0,
            result: overrides.confirmResult || {
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
      return [target(overrides.targetConversation || CONVERSATION)];
    },
    observeTarget: async function () {
      calls.push(["observe"]);
      return {
        browser_target_id: "target-1",
        conversation_id: overrides.observedConversation || CONVERSATION
      };
    },
    dispatchTrustedClick: async function (targetId, point) {
      calls.push(["trusted_click", targetId, point]);
      if (overrides.trustedClickThrows) throw new Error("trusted input result unknown");
      return { dispatched: true };
    },
    message: {
      browser_target_id: "target-1",
      conversation_id: CONVERSATION,
      payload: "resume"
    },
    receiptFactory: function () { return "receipt-1"; }
  };
  return { options, calls };
}

test("Chrome submit refuses wrong conversation before preparation", async function () {
  const f = fixture({ targetConversation: OTHER });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.dispatch_attempted, false);
  assert.equal(result.status, "wrong_conversation_before_dispatch");
  assert.equal(f.calls.length, 0);
});

test("Chrome submit refuses changed machine observation before preparation", async function () {
  const f = fixture({ observedConversation: OTHER });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.dispatch_attempted, false);
  assert.equal(result.status, "machine_target_changed_before_dispatch");
  assert.deepEqual(f.calls, [["observe"]]);
});

test("preparation failure stays pre-dispatch and can remain retryable", async function () {
  const f = fixture({
    prepareResult: {
      ready_to_dispatch: false,
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
  assert.equal(f.calls.some(c => c[0] === "trusted_click"), false);
});

test("trusted browser input is the only dispatch action", async function () {
  const f = fixture();
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.submit_confirmed, true);
  const clicks = f.calls.filter(c => c[0] === "trusted_click");
  assert.deepEqual(clicks, [["trusted_click", "target-1", { x: 120, y: 210 }]]);
  assert.deepEqual(result, {
    dispatch_attempted: true,
    submit_confirmed: true,
    retryable: false,
    status: "submit_confirmed",
    host_submit_receipt: "chrome_submit_receipt-1"
  });
});

test("trusted input exception is result unknown and never retryable", async function () {
  const f = fixture({ trustedClickThrows: true });
  const result = await submitContinuationThroughChrome(f.options);
  assert.deepEqual(result, {
    dispatch_attempted: true,
    submit_confirmed: false,
    retryable: false,
    status: "result_unknown",
    host_submit_receipt: "chrome_submit_receipt-1"
  });
});

test("lost confirmation result after trusted dispatch is result unknown", async function () {
  const f = fixture({ executeThrowsAt: 2 });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.dispatch_attempted, true);
  assert.equal(result.submit_confirmed, false);
  assert.equal(result.retryable, false);
  assert.equal(result.status, "result_unknown");
});

test("unconfirmed page state after trusted dispatch remains result unknown", async function () {
  const f = fixture({
    confirmResult: {
      dispatch_attempted: true,
      submit_confirmed: false,
      retryable: false,
      status: "result_unknown"
    }
  });
  const result = await submitContinuationThroughChrome(f.options);
  assert.equal(result.dispatch_attempted, true);
  assert.equal(result.submit_confirmed, false);
  assert.equal(result.retryable, false);
  assert.equal(result.status, "result_unknown");
});
