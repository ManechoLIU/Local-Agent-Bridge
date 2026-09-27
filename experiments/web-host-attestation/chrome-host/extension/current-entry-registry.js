const GENERATION_PATHS = new Set([
  "/backend-api/f/conversation",
  "/backend-api/conversation"
]);
const CONVERSATION_RE = /^[0-9a-f-]{20,80}$/i;
const SESSION_RECORD_KEY = "lab_current_entry_registry_v1";
const SESSION_RECORD_VERSION = 1;
const MAX_REQUEST_ID_LENGTH = 256;
const MAX_ACTIVE_RECORDS = 64;
const STORED_RECORD_KEYS = new Set(["request_id", "tab_id", "started_at_ms"]);
const STORED_SNAPSHOT_KEYS = new Set(["version", "active"]);

function pathFromUrl(value) {
  try {
    const url = new URL(String(value));
    if (url.protocol !== "https:" || url.hostname !== "chatgpt.com") return null;
    return url.pathname;
  } catch {
    return null;
  }
}

function validTabId(value) {
  const number = Number(value);
  return Number.isInteger(number) && number >= 0 ? number : null;
}

function isServiceWorkerTabId(value) {
  return Number(value) === -1;
}

function requestHeader(details, name) {
  const headers = details && Array.isArray(details.requestHeaders) ? details.requestHeaders : [];
  const expected = String(name).toLowerCase();
  for (const header of headers) {
    if (String(header && header.name || "").toLowerCase() !== expected) continue;
    const value = String(header && header.value || "").trim();
    if (value) return value;
  }
  return null;
}

function isChatGptHttpsOrigin(value) {
  try {
    const url = new URL(String(value));
    return url.protocol === "https:" && url.hostname === "chatgpt.com";
  } catch {
    return false;
  }
}

function conversationIdFromChatgptUrl(value) {
  try {
    const url = new URL(String(value));
    if (url.protocol !== "https:" || url.hostname !== "chatgpt.com") return null;
    const parts = url.pathname.split("/").filter(Boolean);
    const index = parts.lastIndexOf("c");
    const conversation = index >= 0 ? parts[index + 1] : null;
    return conversation && CONVERSATION_RE.test(conversation) ? conversation : null;
  } catch {
    return null;
  }
}

function hasChatGptInitiator(details) {
  return isChatGptHttpsOrigin(details && details.initiator)
    || isChatGptHttpsOrigin(details && details.originUrl)
    || isChatGptHttpsOrigin(requestHeader(details, "Origin"));
}

function generationConversationIds(details) {
  const ids = new Set();
  for (const value of [
    details && details.referer,
    details && details.documentUrl,
    requestHeader(details, "Referer"),
    details && details.url
  ]) {
    const conversationId = conversationIdFromChatgptUrl(value);
    if (conversationId) ids.add(conversationId);
  }
  return ids;
}

function bindServiceWorkerGenerationTab(targets, conversationId) {
  const tabIds = [];
  const seen = new Set();
  for (const target of Array.isArray(targets) ? targets : []) {
    if (!target || target.type !== "page") continue;
    const tabId = validTabId(target.tabId);
    if (tabId == null) continue;
    if (conversationIdFromChatgptUrl(target.url) !== conversationId) continue;
    if (seen.has(tabId)) continue;
    seen.add(tabId);
    tabIds.push(tabId);
  }
  if (tabIds.length === 0) return null;
  if (tabIds.length !== 1) {
    throw new Error("HOST_CURRENT_ENTRY_AMBIGUOUS: service worker generation matches multiple ChatGPT tabs");
  }
  return tabIds[0];
}

async function generationAnchorSha256(requestIds) {
  const bytes = new TextEncoder().encode(JSON.stringify([...requestIds].sort()));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function sessionStoreFailure(cause) {
  const error = new Error("HOST_CURRENT_ENTRY_UNAVAILABLE: current-entry session store is unavailable or corrupt");
  error.cause = cause;
  return error;
}

function positiveSafeInteger(value) {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0 ? value : null;
}

function hasOnlyKeys(value, allowedKeys) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const keys = Object.keys(value);
  return keys.length === allowedKeys.size && keys.every((key) => allowedKeys.has(key));
}

function storedRecord(value, now, futureSkewMs) {
  if (!hasOnlyKeys(value, STORED_SNAPSHOT_KEYS)) return null;
  if (value.version !== SESSION_RECORD_VERSION || !Array.isArray(value.active)) return null;
  if (value.active.length > MAX_ACTIVE_RECORDS) return null;
  const active = new Map();
  for (const record of value.active) {
    if (!hasOnlyKeys(record, STORED_RECORD_KEYS)) return null;
    const requestId = typeof record?.request_id === "string" ? record.request_id.trim() : "";
    const tabId = typeof record?.tab_id === "number" ? validTabId(record.tab_id) : null;
    const startedAtMs = positiveSafeInteger(record?.started_at_ms);
    if (!requestId || requestId.length > MAX_REQUEST_ID_LENGTH || tabId == null || startedAtMs == null || active.has(requestId)) return null;
    if (startedAtMs - now > futureSkewMs) return null;
    active.set(requestId, Object.freeze({
      request_id: requestId,
      tab_id: tabId,
      started_at_ms: startedAtMs
    }));
  }
  return active;
}

export function isChatGptGenerationRequest(details) {
  if (!details || String(details.method || "").toUpperCase() !== "POST") return false;
  if (!GENERATION_PATHS.has(pathFromUrl(details.url))) return false;
  if (validTabId(details.tabId) != null) return true;
  return isServiceWorkerTabId(details.tabId) && hasChatGptInitiator(details);
}

export function createCurrentEntryRegistry({
  clock = Date.now,
  maxAgeMs = 30 * 60 * 1000,
  futureSkewMs = 5000,
  sessionStore = globalThis.chrome?.storage?.session,
  listChatGptPages
} = {}) {
  if (!sessionStore || typeof sessionStore.get !== "function" || typeof sessionStore.set !== "function") {
    throw new Error("chrome.storage.session is required for current-entry registry");
  }
  if (positiveSafeInteger(maxAgeMs) == null) {
    throw new Error("current-entry max age must be a positive safe integer");
  }
  if (!Number.isSafeInteger(futureSkewMs) || futureSkewMs < 0) {
    throw new Error("current-entry future skew must be a non-negative safe integer");
  }
  const active = new Map();
  let initialized = false;
  let failure = null;
  let queue = Promise.resolve();

  function currentTime() {
    const now = positiveSafeInteger(clock());
    if (now == null) {
      failure = new Error("HOST_CURRENT_ENTRY_UNAVAILABLE: current-entry clock timestamp is invalid");
      throw failure;
    }
    return now;
  }

  async function initialize() {
    if (initialized) return;
    try {
      const now = currentTime();
      const entries = await sessionStore.get(SESSION_RECORD_KEY);
      if (!entries || typeof entries !== "object" || Array.isArray(entries)) throw new Error("invalid session-store response");
      if (Object.hasOwn(entries, SESSION_RECORD_KEY)) {
        const restored = storedRecord(entries[SESSION_RECORD_KEY], now, futureSkewMs);
        if (!restored) throw new Error("invalid current-entry session record");
        active.clear();
        for (const [requestId, record] of restored) active.set(requestId, record);
      }
      initialized = true;
    } catch (cause) {
      failure = failure || sessionStoreFailure(cause);
      throw failure;
    }
  }

  async function persist() {
    try {
      await sessionStore.set({
        [SESSION_RECORD_KEY]: {
          version: SESSION_RECORD_VERSION,
          active: Array.from(active.values(), (record) => ({ ...record }))
        }
      });
    } catch (cause) {
      failure = sessionStoreFailure(cause);
      throw failure;
    }
  }

  function serialize(operation) {
    const task = queue.then(async function () {
      if (failure) throw failure;
      await initialize();
      if (failure) throw failure;
      return await operation();
    });
    queue = task.catch(() => {});
    return task;
  }

  async function resolveObservedTabId(details) {
    const direct = validTabId(details && details.tabId);
    if (direct != null) return direct;
    if (!isServiceWorkerTabId(details && details.tabId)) return null;
    const conversationIds = generationConversationIds(details);
    if (conversationIds.size === 0) return null;
    if (conversationIds.size !== 1) {
      throw new Error("HOST_CURRENT_ENTRY_AMBIGUOUS: service worker generation conversation is ambiguous");
    }
    if (typeof listChatGptPages !== "function") {
      throw new Error("HOST_CURRENT_ENTRY_UNAVAILABLE: service worker generation tab is unavailable");
    }
    const conversationId = [...conversationIds][0];
    const tabId = bindServiceWorkerGenerationTab(await listChatGptPages(), conversationId);
    if (tabId == null) {
      throw new Error("HOST_CURRENT_ENTRY_UNAVAILABLE: service worker generation tab is unavailable");
    }
    return tabId;
  }

  function prune(now) {
    let pruned = false;
    for (const [requestId, record] of active) {
      if (now - record.started_at_ms >= maxAgeMs) {
        active.delete(requestId);
        pruned = true;
      }
    }
    return pruned;
  }

  async function resolveCurrent() {
    return await serialize(async function () {
      const now = currentTime();
      for (const record of active.values()) {
        if (record.started_at_ms - now > futureSkewMs) {
          failure = new Error("HOST_CURRENT_ENTRY_UNAVAILABLE: active generation timestamp exceeds allowed future skew");
          throw failure;
        }
      }
      if (prune(now)) await persist();
      const byTab = new Map();
      for (const record of active.values()) {
        if (!byTab.has(record.tab_id)) byTab.set(record.tab_id, []);
        byTab.get(record.tab_id).push(record.request_id);
      }
      if (byTab.size === 0) return null;
      if (byTab.size !== 1) throw new Error("HOST_CURRENT_ENTRY_AMBIGUOUS: multiple ChatGPT generations are active");
      const [[tabId, requestIds]] = byTab.entries();
      return Object.freeze({
        tab_id: tabId,
        generation_anchor_sha256: await generationAnchorSha256(requestIds),
        observed_at_ms: now
      });
    });
  }

  return Object.freeze({
    async observeStart(details) {
      if (!isChatGptGenerationRequest(details)) return false;
      const requestId = String(details.requestId || "").trim();
      if (!requestId) return false;
      const startedAtMs = currentTime();
      return await serialize(async function () {
        if (requestId.length > MAX_REQUEST_ID_LENGTH) {
          failure = new Error("HOST_CURRENT_ENTRY_UNAVAILABLE: request id exceeds maximum length");
          throw failure;
        }
        const tabId = await resolveObservedTabId(details);
        if (tabId == null) return false;
        const existing = active.get(requestId);
        if (existing) {
          if (existing.tab_id === tabId) return true;
          failure = new Error("HOST_CURRENT_ENTRY_UNAVAILABLE: request id was observed in another tab");
          throw failure;
        }
        if (active.size >= MAX_ACTIVE_RECORDS) {
          failure = new Error("HOST_CURRENT_ENTRY_UNAVAILABLE: active generation record count exceeds maximum");
          throw failure;
        }
        active.set(requestId, Object.freeze({
          request_id: requestId,
          tab_id: tabId,
          started_at_ms: startedAtMs
        }));
        await persist();
        return true;
      });
    },

    async observeFinish(details) {
      const requestId = String(details?.requestId || "").trim();
      if (!requestId) return false;
      return await serialize(async function () {
        const removed = active.delete(requestId);
        if (removed) await persist();
        return removed;
      });
    },

    resolveCurrent,

    async isStillCurrent(candidate) {
      if (!candidate || typeof candidate.generation_anchor_sha256 !== "string") return false;
      const current = await resolveCurrent();
      if (!current || current.tab_id !== candidate.tab_id) return false;
      return candidate.generation_anchor_sha256 === current.generation_anchor_sha256;
    }
  });
}

export function selectCurrentEntryTarget(targets, candidate) {
  const tabId = validTabId(candidate?.tab_id);
  if (tabId == null) throw new Error("current-entry candidate tab_id is invalid");
  const matches = (Array.isArray(targets) ? targets : []).filter((target) => {
    if (!target || target.type !== "page" || Number(target.tabId) !== tabId) return false;
    try {
      const url = new URL(String(target.url || ""));
      return url.protocol === "https:" && url.hostname === "chatgpt.com";
    } catch {
      return false;
    }
  });
  if (matches.length === 0) throw new Error("HOST_CURRENT_ENTRY_UNAVAILABLE: active generation tab has no ChatGPT page target");
  if (matches.length !== 1) throw new Error("HOST_CURRENT_ENTRY_AMBIGUOUS: active generation tab maps to multiple ChatGPT targets");
  return matches[0];
}
