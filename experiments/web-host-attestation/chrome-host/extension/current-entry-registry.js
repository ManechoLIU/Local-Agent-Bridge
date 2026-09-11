const GENERATION_PATHS = new Set([
  "/backend-api/f/conversation",
  "/backend-api/conversation"
]);

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

async function generationAnchorSha256(requestIds) {
  const bytes = new TextEncoder().encode(JSON.stringify([...requestIds].sort()));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function isChatGptGenerationRequest(details) {
  if (!details || String(details.method || "").toUpperCase() !== "POST") return false;
  const tabId = validTabId(details.tabId);
  if (tabId == null) return false;
  return GENERATION_PATHS.has(pathFromUrl(details.url));
}

export function createCurrentEntryRegistry({ clock = Date.now, maxAgeMs = 30 * 60 * 1000 } = {}) {
  const active = new Map();

  function prune(now) {
    for (const [requestId, record] of active) {
      if (now - record.started_at_ms > maxAgeMs) active.delete(requestId);
    }
  }

  return Object.freeze({
    observeStart(details) {
      if (!isChatGptGenerationRequest(details)) return false;
      const requestId = String(details.requestId || "").trim();
      if (!requestId) return false;
      active.set(requestId, {
        request_id: requestId,
        tab_id: validTabId(details.tabId),
        started_at_ms: Number(clock())
      });
      return true;
    },

    observeFinish(details) {
      const requestId = String(details?.requestId || "").trim();
      return requestId ? active.delete(requestId) : false;
    },

    async resolveCurrent() {
      const now = Number(clock());
      prune(now);
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
    },

    async isStillCurrent(candidate) {
      if (!candidate || typeof candidate.generation_anchor_sha256 !== "string") return false;
      const current = await this.resolveCurrent();
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
