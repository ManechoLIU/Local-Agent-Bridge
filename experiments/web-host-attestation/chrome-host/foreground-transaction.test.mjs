import test from "node:test";
import assert from "node:assert/strict";
import { withConditionalForegroundTransaction } from "./extension/foreground-transaction.js";

function fixture() {
  const calls = [];
  let active = { id: 1, windowId: 10 };
  const tabs = new Map([
    [1, { id: 1, windowId: 10 }],
    [2, { id: 2, windowId: 20 }],
    [3, { id: 3, windowId: 30 }]
  ]);
  return {
    calls,
    setActive(tabId) { active = tabs.get(tabId); },
    chromeApi: {
      tabs: {
        async query() { return [active]; },
        async get(id) { return tabs.get(id); },
        async update(id, props) {
          calls.push(["tab_update", id, props]);
          if (props.active) active = tabs.get(id);
          return tabs.get(id);
        }
      },
      windows: {
        async update(id, props) {
          calls.push(["window_update", id, props]);
          return { id, ...props };
        }
      }
    }
  };
}

test("foreground fallback restores the previous active tab when user did not intervene", async () => {
  const f = fixture();
  const result = await withConditionalForegroundTransaction({
    chromeApi: f.chromeApi,
    targetTabId: 2,
    run: async () => "ok"
  });
  assert.equal(result.result, "ok");
  assert.equal(result.focus_transaction.activated_by_host, true);
  assert.equal(result.focus_transaction.restored_previous_active, true);
  assert.equal(result.focus_transaction.user_changed_focus, false);
  assert.deepEqual(f.calls.at(-2), ["tab_update", 1, { active: true }]);
});

test("foreground fallback does not pull user back after user switches during transaction", async () => {
  const f = fixture();
  const result = await withConditionalForegroundTransaction({
    chromeApi: f.chromeApi,
    targetTabId: 2,
    run: async () => {
      f.setActive(3);
      return "ok";
    }
  });
  assert.equal(result.focus_transaction.restored_previous_active, false);
  assert.equal(result.focus_transaction.user_changed_focus, true);
  assert.equal(f.calls.some(call => call[0] === "tab_update" && call[1] === 1), false);
});
