import test from "node:test";
import assert from "node:assert/strict";
import {
  createChromeSubmitJournal,
  recoverPreparedSubmitJournals
} from "./extension/submit-journal.js";

function storageFixture(seed = {}) {
  const values = { ...seed };
  return {
    values,
    chromeApi: {
      storage: {
        local: {
          async get(key) {
            if (key == null) return { ...values };
            return Object.prototype.hasOwnProperty.call(values, key)
              ? { [key]: values[key] }
              : {};
          },
          async set(input) { Object.assign(values, input); },
          async remove(key) { delete values[key]; }
        }
      }
    }
  };
}

const RECORD = {
  wake_id: "wake-1",
  browser_target_id: "target-1",
  conversation_id: "6a9e4158-cc8c-83ea-9763-05a7fd51f597",
  target_generation: 4,
  ownership_generation: 8,
  payload_sha256: "a".repeat(64),
  phase: "prepared",
  dispatch_attempted: false,
  baseline_user_message_count: 2,
  updated_at_ms: 1
};

test("composer write survives Host crash and a restarted journal recovers only by exact-hash rollback", async () => {
  const f = storageFixture();
  const firstProcessJournal = createChromeSubmitJournal(f.chromeApi);
  await firstProcessJournal.put(RECORD);

  const restartedJournal = createChromeSubmitJournal(f.chromeApi);
  const result = await recoverPreparedSubmitJournals({
    journal: restartedJournal,
    rollbackPreparedByHash: async record => {
      assert.equal(record.payload_sha256, "a".repeat(64));
      return { cleared: true };
    }
  });
  assert.equal(result[0].recovered, true);
  assert.equal((await restartedJournal.list()).length, 0);
});

test("durable journal persists baseline_user_message_count and keeps missing baseline null", async () => {
  const f = storageFixture();
  const journal = createChromeSubmitJournal(f.chromeApi);
  const stored = await journal.put({ ...RECORD, phase: "result_unknown", dispatch_attempted: true });
  assert.equal(stored.baseline_user_message_count, 2);
  const [kept] = await journal.list();
  assert.equal(kept.baseline_user_message_count, 2);

  const missing = await journal.put({
    ...RECORD,
    wake_id: "wake-missing-baseline",
    baseline_user_message_count: undefined
  });
  assert.equal(missing.baseline_user_message_count, null);

  const invalid = await journal.put({
    ...RECORD,
    wake_id: "wake-invalid-baseline",
    baseline_user_message_count: -1
  });
  assert.equal(invalid.baseline_user_message_count, null);
});

test("dispatch_armed crash is promoted to RESULT_UNKNOWN without rollback or replay", async () => {
  const f = storageFixture();
  const journal = createChromeSubmitJournal(f.chromeApi);
  await journal.put({ ...RECORD, phase: "dispatch_armed", dispatch_attempted: false });
  let rollbackCalls = 0;
  const result = await recoverPreparedSubmitJournals({
    journal,
    rollbackPreparedByHash: async () => {
      rollbackCalls += 1;
      return { cleared: true };
    }
  });
  assert.equal(rollbackCalls, 0);
  assert.equal(result[0].reason, "dispatch_armed_crash_result_unknown");
  const [kept] = await journal.list();
  assert.equal(kept.phase, "result_unknown");
  assert.equal(kept.dispatch_attempted, true);
  assert.equal(kept.baseline_user_message_count, 2);
});

test("journal with possible dispatch is retained and never rolled back", async () => {
  const f = storageFixture();
  const journal = createChromeSubmitJournal(f.chromeApi);
  await journal.put({ ...RECORD, phase: "dispatch_started", dispatch_attempted: true });
  let rollbackCalls = 0;
  const result = await recoverPreparedSubmitJournals({
    journal,
    rollbackPreparedByHash: async () => {
      rollbackCalls += 1;
      return { cleared: true };
    }
  });
  assert.equal(rollbackCalls, 0);
  assert.equal(result[0].reason, "dispatch_may_have_occurred");
  assert.equal((await journal.list()).length, 1);
});

test("mismatched composer is retained and never deleted", async () => {
  const f = storageFixture();
  const journal = createChromeSubmitJournal(f.chromeApi);
  await journal.put(RECORD);
  const result = await recoverPreparedSubmitJournals({
    journal,
    rollbackPreparedByHash: async () => ({ cleared: false, already_absent: false })
  });
  assert.equal(result[0].recovered, false);
  const [kept] = await journal.list();
  assert.equal(kept.phase, "rollback_unconfirmed");
});
