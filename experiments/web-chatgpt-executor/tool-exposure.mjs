export const LOCAL_AGENT_BRIDGE_APP = "Local Agent Bridge";

export const EXPOSURE_STATE = Object.freeze({
  CONFIRMED: "CONFIRMED",
  ABSENT_REPAIRABLE: "ABSENT_REPAIRABLE",
  UNAVAILABLE: "UNAVAILABLE",
  UNKNOWN: "UNKNOWN"
});

export const SILENT_FALLBACK_APPS = Object.freeze([
  "AI-Bridge",
  "Local Bridge Identity Probe"
]);

export function canonicalPromptText(value) {
  return String(value ?? "").replace(/\r\n?/g, "\n");
}

function normalize(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim().toLocaleLowerCase();
}

export function mentionToken(appName) {
  const name = String(appName || "").trim();
  return name ? "@" + name : "";
}

export function uniqueAppNames(values) {
  const seen = new Set();
  const names = [];
  for (const value of Array.isArray(values) ? values : []) {
    const name = String(value || "").trim();
    if (!name) continue;
    const key = normalize(name);
    if (seen.has(key)) continue;
    seen.add(key);
    names.push(name);
  }
  return names;
}

export function resolveRequiredApps(input) {
  if (!input || typeof input !== "object") return [];
  const nested = input.tool_exposure;
  if (nested && typeof nested === "object" && Array.isArray(nested.required_apps)) {
    const names = uniqueAppNames(nested.required_apps);
    if (names.length) return names;
  }
  if (Array.isArray(input.required_apps)) {
    const names = uniqueAppNames(input.required_apps);
    if (names.length) return names;
  }
  if (input.local_tool_app_required === true) return [LOCAL_AGENT_BRIDGE_APP];
  if (typeof input.local_tool_app_required === "string") {
    return uniqueAppNames([input.local_tool_app_required]);
  }
  return [];
}

export function classifyObservedExposure({
  required,
  observed,
  available
} = {}) {
  if (available === false) return EXPOSURE_STATE.UNAVAILABLE;
  if (!Array.isArray(observed)) return EXPOSURE_STATE.UNKNOWN;
  const needed = String(required || "").trim();
  if (!needed) return EXPOSURE_STATE.UNKNOWN;
  const hit = observed.some((name) => normalize(name) === normalize(needed));
  return hit ? EXPOSURE_STATE.CONFIRMED : EXPOSURE_STATE.ABSENT_REPAIRABLE;
}

export function isSilentFallbackApp(name) {
  return SILENT_FALLBACK_APPS.some((item) => normalize(item) === normalize(name));
}

export function assertNotSilentFallback(required, candidate) {
  const value = String(candidate || "").trim();
  if (!value) return;
  if (normalize(value) === normalize(required)) return;
  if (isSilentFallbackApp(value)) {
    throw new Error("silent fallback to " + value + " is forbidden");
  }
}

function emptyExposure(appName, state = EXPOSURE_STATE.UNKNOWN) {
  return {
    app: appName,
    state,
    confirmed: state === EXPOSURE_STATE.CONFIRMED,
    exposed: state === EXPOSURE_STATE.CONFIRMED,
    action: null,
    attempts: 0,
    observed_apps: [],
    ui_mention_used: false
  };
}

export function normalizeExposureResult(appName, raw = {}) {
  const app = String(appName || raw.app || "").trim();
  const hasObservedEvidence = Array.isArray(raw.observed_apps);
  const observed = uniqueAppNames(raw.observed_apps);
  for (const name of observed) assertNotSilentFallback(app, name);
  if (raw.app) assertNotSilentFallback(app, raw.app);

  // Never trust adapter-declared CONFIRMED/exposed/confirmed booleans as proof.
  // Per-message exposure is confirmed only by exact current-composer evidence.
  let state;
  if (raw.available === false || String(raw.state || "").toUpperCase() === EXPOSURE_STATE.UNAVAILABLE) {
    state = EXPOSURE_STATE.UNAVAILABLE;
  } else if (hasObservedEvidence) {
    state = classifyObservedExposure({
      required: app,
      observed,
      available: raw.available
    });
  } else {
    state = EXPOSURE_STATE.UNKNOWN;
  }

  const attempts = Number.isInteger(raw.attempts)
    ? raw.attempts
    : Number.parseInt(String(raw.attempts || 0), 10) || 0;

  return {
    app,
    state,
    confirmed: state === EXPOSURE_STATE.CONFIRMED,
    exposed: state === EXPOSURE_STATE.CONFIRMED,
    action: raw.action || null,
    attempts,
    observed_apps: observed,
    ui_mention_used: raw.ui_mention_used === true
  };
}

export function failClosedExposure(result) {
  const error = new Error(
    "required Web tool app is not confirmed for this message: " +
      String(result?.app || "") +
      " (" +
      String(result?.state || EXPOSURE_STATE.UNAVAILABLE) +
      ")"
  );
  error.exposure = result;
  return error;
}

export async function ensureAppExposure(browser, appName, options = {}) {
  const app = String(appName || "").trim();
  if (!app) throw new Error("app name is required");

  let observed = null;
  if (typeof browser?.observeAppExposure === "function") {
    observed = normalizeExposureResult(app, await browser.observeAppExposure(app, options));
    if (observed.state === EXPOSURE_STATE.CONFIRMED) {
      return {
        ...observed,
        action: "already_exposed",
        attempts: 0,
        confirmed: true,
        exposed: true
      };
    }
  }

  let selected = null;
  if (typeof browser?.selectApp === "function") {
    selected = normalizeExposureResult(app, await browser.selectApp(app, options));
  } else if (typeof browser?.ensureAppExposure === "function") {
    selected = normalizeExposureResult(app, await browser.ensureAppExposure(app, options));
  } else {
    throw failClosedExposure({
      ...emptyExposure(app, EXPOSURE_STATE.UNAVAILABLE),
      action: "unavailable",
      observed_apps: observed?.observed_apps || []
    });
  }

  let confirmed = selected;
  if (typeof browser?.observeAppExposure === "function") {
    confirmed = normalizeExposureResult(app, await browser.observeAppExposure(app, options));
  }

  if (confirmed.state === EXPOSURE_STATE.CONFIRMED) {
    return {
      ...confirmed,
      action: "selected",
      attempts: 1,
      ui_mention_used: selected.ui_mention_used === true
    };
  }

  const state =
    selected.state === EXPOSURE_STATE.UNAVAILABLE
      ? EXPOSURE_STATE.UNAVAILABLE
      : confirmed.state === EXPOSURE_STATE.UNKNOWN
        ? EXPOSURE_STATE.ABSENT_REPAIRABLE
        : confirmed.state;

  throw failClosedExposure({
    ...confirmed,
    state,
    confirmed: false,
    exposed: false,
    action: "failed",
    attempts: 1,
    ui_mention_used: selected.ui_mention_used === true
  });
}

export function summarizeExposure({ required_apps = [], results = [] } = {}) {
  const apps = uniqueAppNames(required_apps);
  if (!apps.length) {
    return {
      required_apps: [],
      state: EXPOSURE_STATE.UNKNOWN,
      confirmed: false,
      attempts: 0,
      action: null,
      observed_apps: [],
      ui_mention_used: false,
      results: []
    };
  }

  const normalized = results.map((item, index) =>
    normalizeExposureResult(apps[index] || item.app, item)
  );
  const states = normalized.map((item) => item.state);
  let state = EXPOSURE_STATE.CONFIRMED;
  if (states.some((value) => value === EXPOSURE_STATE.UNAVAILABLE)) {
    state = EXPOSURE_STATE.UNAVAILABLE;
  } else if (states.some((value) => value === EXPOSURE_STATE.ABSENT_REPAIRABLE)) {
    state = EXPOSURE_STATE.ABSENT_REPAIRABLE;
  } else if (states.some((value) => value === EXPOSURE_STATE.UNKNOWN) || !states.length) {
    state = EXPOSURE_STATE.UNKNOWN;
  } else if (!states.every((value) => value === EXPOSURE_STATE.CONFIRMED)) {
    state = EXPOSURE_STATE.UNKNOWN;
  }

  return {
    required_apps: apps,
    state,
    confirmed: state === EXPOSURE_STATE.CONFIRMED,
    attempts: normalized.reduce((sum, item) => sum + Number(item.attempts || 0), 0),
    action: normalized.length === 1 ? normalized[0].action : "aggregate",
    observed_apps: uniqueAppNames(normalized.flatMap((item) => item.observed_apps || [])),
    ui_mention_used: normalized.some((item) => item.ui_mention_used === true),
    results: normalized.map((item) => ({
      app: item.app,
      state: item.state,
      confirmed: item.confirmed,
      action: item.action,
      attempts: item.attempts,
      observed_apps: item.observed_apps,
      ui_mention_used: item.ui_mention_used
    }))
  };
}

export async function ensureRequiredAppsExposed(browser, apps, options = {}) {
  const required = uniqueAppNames(apps);
  if (!required.length) {
    return summarizeExposure({ required_apps: [], results: [] });
  }
  const results = [];
  try {
    for (const app of required) {
      results.push(await ensureAppExposure(browser, app, options));
    }
  } catch (error) {
    if (error?.exposure) {
      error.exposure = summarizeExposure({
        required_apps: required,
        results: [...results, error.exposure]
      });
    }
    throw error;
  }
  return summarizeExposure({ required_apps: required, results });
}

export function initialToolExposure(input) {
  const requiredApps = resolveRequiredApps(input);
  return {
    required_apps: requiredApps,
    state: EXPOSURE_STATE.UNKNOWN,
    confirmed: false,
    attempts: 0,
    action: null,
    observed_apps: [],
    ui_mention_used: false
  };
}
