const PREFIX = "lab_host_submit_journal_v1:";

function requiredText(value, name, max = 512) {
  const text = String(value == null ? "" : value).trim();
  if (!text) throw new Error(name + " is required");
  if (text.length > max) throw new Error(name + " exceeds limit");
  return text;
}

function requiredGeneration(value, name) {
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < 1) {
    throw new Error(name + " must be a positive integer");
  }
  return number;
}

function normalizeRecord(input) {
  const value = input && typeof input === "object" ? input : {};
  return Object.freeze({
    schema_version: 1,
    wake_id: requiredText(value.wake_id, "wake_id", 256),
    browser_target_id: requiredText(value.browser_target_id, "browser_target_id", 256),
    conversation_id: requiredText(value.conversation_id, "conversation_id", 128),
    target_generation: requiredGeneration(value.target_generation, "target_generation"),
    ownership_generation: requiredGeneration(value.ownership_generation, "ownership_generation"),
    payload_sha256: requiredText(value.payload_sha256, "payload_sha256", 64),
    phase: requiredText(value.phase, "phase", 128),
    dispatch_attempted: value.dispatch_attempted === true,
    updated_at_ms: Number.isSafeInteger(Number(value.updated_at_ms))
      ? Number(value.updated_at_ms)
      : Date.now()
  });
}

export function createChromeSubmitJournal(chromeApi) {
  const area = chromeApi && chromeApi.storage && chromeApi.storage.local;
  if (!area || typeof area.get !== "function" ||
      typeof area.set !== "function" || typeof area.remove !== "function") {
    throw new Error("chrome.storage.local is required for submit journal");
  }

  return Object.freeze({
    async get(wakeId) {
      const key = PREFIX + requiredText(wakeId, "wake_id", 256);
      const values = await area.get(key);
      const raw = values && values[key];
      return raw ? normalizeRecord(raw) : null;
    },
    async list() {
      const values = await area.get(null);
      return Object.entries(values || {})
        .filter(([key]) => key.startsWith(PREFIX))
        .map(([, value]) => normalizeRecord(value));
    },
    async put(record) {
      const value = normalizeRecord(record);
      const key = PREFIX + value.wake_id;
      await area.set({ [key]: value });
      return value;
    },
    async remove(wakeId) {
      await area.remove(PREFIX + requiredText(wakeId, "wake_id", 256));
    }
  });
}

export async function recoverPreparedSubmitJournals(options) {
  const journal = options && options.journal;
  const rollbackPreparedByHash = options && options.rollbackPreparedByHash;
  if (!journal || typeof journal.list !== "function" ||
      typeof journal.put !== "function" || typeof journal.remove !== "function") {
    throw new Error("submit journal is required");
  }
  if (typeof rollbackPreparedByHash !== "function") {
    throw new Error("rollbackPreparedByHash is required");
  }

  const results = [];
  for (const record of await journal.list()) {
    if (record.dispatch_attempted) {
      results.push(Object.freeze({
        wake_id: record.wake_id,
        recovered: false,
        retained: true,
        reason: "dispatch_may_have_occurred"
      }));
      continue;
    }
    let rollback;
    try {
      rollback = await rollbackPreparedByHash(record);
    } catch (error) {
      await journal.put({
        ...record,
        phase: "rollback_failed",
        updated_at_ms: Date.now()
      });
      results.push(Object.freeze({
        wake_id: record.wake_id,
        recovered: false,
        retained: true,
        reason: String(error && error.message || error || "rollback_failed")
      }));
      continue;
    }
    if (rollback && rollback.cleared === true) {
      await journal.remove(record.wake_id);
      results.push(Object.freeze({
        wake_id: record.wake_id,
        recovered: true,
        retained: false,
        reason: "exact_payload_rolled_back"
      }));
    } else if (rollback && rollback.already_absent === true) {
      await journal.remove(record.wake_id);
      results.push(Object.freeze({
        wake_id: record.wake_id,
        recovered: true,
        retained: false,
        reason: "payload_already_absent"
      }));
    } else {
      await journal.put({
        ...record,
        phase: "rollback_unconfirmed",
        updated_at_ms: Date.now()
      });
      results.push(Object.freeze({
        wake_id: record.wake_id,
        recovered: false,
        retained: true,
        reason: "composer_not_exact_owned_payload"
      }));
    }
  }
  return results;
}
