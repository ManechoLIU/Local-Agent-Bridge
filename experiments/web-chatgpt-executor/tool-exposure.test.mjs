import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  EXPOSURE_STATE,
  LOCAL_AGENT_BRIDGE_APP,
  SILENT_FALLBACK_APPS,
  classifyObservedExposure,
  ensureAppExposure,
  ensureRequiredAppsExposed,
  mentionToken,
  normalizeExposureResult,
  resolveRequiredApps,
  summarizeExposure
} from "./tool-exposure.mjs";

function fakeBrowser({
  composerApps = [],
  failConfirm = false,
  menuApps = [LOCAL_AGENT_BRIDGE_APP, ...SILENT_FALLBACK_APPS]
} = {}) {
  const state = {
    composerApps: new Set(composerApps),
    menuApps: [...menuApps],
    observeCalls: [],
    selectCalls: [],
    ensureCalls: []
  };

  return {
    state,
    async observeAppExposure(appName) {
      state.observeCalls.push(appName);
      const exposed = state.composerApps.has(appName);
      return {
        app: appName,
        exposed,
        confirmed: exposed,
        state: exposed ? EXPOSURE_STATE.CONFIRMED : EXPOSURE_STATE.ABSENT_REPAIRABLE,
        observed_apps: [...state.composerApps]
      };
    },
    async selectApp(appName) {
      state.selectCalls.push(appName);
      if (failConfirm || !state.menuApps.includes(appName)) {
        return {
          app: appName,
          exposed: false,
          confirmed: false,
          state: failConfirm ? EXPOSURE_STATE.UNAVAILABLE : EXPOSURE_STATE.ABSENT_REPAIRABLE,
          observed_apps: [...state.composerApps]
        };
      }
      state.composerApps.add(appName);
      return {
        app: appName,
        exposed: true,
        confirmed: true,
        state: EXPOSURE_STATE.CONFIRMED,
        observed_apps: [...state.composerApps],
        ui_mention_used: true
      };
    }
  };
}

test("resolveRequiredApps is explicit and does not default to Local Agent Bridge", () => {
  assert.deepEqual(resolveRequiredApps({}), []);
  assert.deepEqual(resolveRequiredApps({ prompt: "use local tools" }), []);
  assert.deepEqual(resolveRequiredApps({ local_tool_app_required: false }), []);
  assert.deepEqual(resolveRequiredApps({ local_tool_app_required: true }), [
    LOCAL_AGENT_BRIDGE_APP
  ]);
  assert.deepEqual(resolveRequiredApps({ local_tool_app_required: "Local Agent Bridge" }), [
    LOCAL_AGENT_BRIDGE_APP
  ]);
  assert.deepEqual(resolveRequiredApps({ required_apps: ["Local Agent Bridge", "Files"] }), [
    LOCAL_AGENT_BRIDGE_APP,
    "Files"
  ]);
  assert.deepEqual(
    resolveRequiredApps({
      tool_exposure: { required_apps: ["Local Agent Bridge"] },
      required_apps: ["ignored-when-nested-present"]
    }),
    [LOCAL_AGENT_BRIDGE_APP]
  );
});

test("classifyObservedExposure stays distinct from backend/tunnel health", () => {
  assert.equal(
    classifyObservedExposure({ required: LOCAL_AGENT_BRIDGE_APP, observed: [LOCAL_AGENT_BRIDGE_APP] }),
    EXPOSURE_STATE.CONFIRMED
  );
  assert.equal(
    classifyObservedExposure({ required: LOCAL_AGENT_BRIDGE_APP, observed: [] }),
    EXPOSURE_STATE.ABSENT_REPAIRABLE
  );
  assert.equal(
    classifyObservedExposure({ required: LOCAL_AGENT_BRIDGE_APP, observed: null, available: false }),
    EXPOSURE_STATE.UNAVAILABLE
  );
  assert.equal(classifyObservedExposure({ required: LOCAL_AGENT_BRIDGE_APP }), EXPOSURE_STATE.UNKNOWN);
});



test("adapter state or booleans cannot prove exposure without exact current observed app", () => {
  for (const raw of [
    { state: EXPOSURE_STATE.CONFIRMED, observed_apps: [] },
    { confirmed: true, observed_apps: [] },
    { exposed: true, observed_apps: [] }
  ]) {
    const normalized = normalizeExposureResult(LOCAL_AGENT_BRIDGE_APP, raw);
    assert.notEqual(normalized.state, EXPOSURE_STATE.CONFIRMED);
    assert.equal(normalized.confirmed, false);
  }

  const valid = normalizeExposureResult(LOCAL_AGENT_BRIDGE_APP, {
    state: EXPOSURE_STATE.CONFIRMED,
    observed_apps: [LOCAL_AGENT_BRIDGE_APP]
  });
  assert.equal(valid.state, EXPOSURE_STATE.CONFIRMED);
  assert.equal(valid.confirmed, true);
});

test("claimed CONFIRMED with only silent fallback evidence fails closed", () => {
  assert.throws(
    () =>
      normalizeExposureResult(LOCAL_AGENT_BRIDGE_APP, {
        state: EXPOSURE_STATE.CONFIRMED,
        observed_apps: ["AI-Bridge"]
      }),
    /silent fallback/i
  );
});

test("already exposed app is confirmed without a redundant select", async () => {
  const browser = fakeBrowser({ composerApps: [LOCAL_AGENT_BRIDGE_APP] });
  const result = await ensureAppExposure(browser, LOCAL_AGENT_BRIDGE_APP);
  assert.equal(result.state, EXPOSURE_STATE.CONFIRMED);
  assert.equal(result.action, "already_exposed");
  assert.equal(result.attempts, 0);
  assert.deepEqual(browser.state.selectCalls, []);
  assert.equal(browser.state.observeCalls.length, 1);
});

test("absent app after hydration is reselected and confirmed before callers may send", async () => {
  const browser = fakeBrowser({ composerApps: [] });
  const result = await ensureAppExposure(browser, LOCAL_AGENT_BRIDGE_APP);
  assert.equal(result.state, EXPOSURE_STATE.CONFIRMED);
  assert.equal(result.action, "selected");
  assert.equal(result.attempts, 1);
  assert.deepEqual(browser.state.selectCalls, [LOCAL_AGENT_BRIDGE_APP]);
  assert.ok(browser.state.observeCalls.length >= 2);
});

test("unconfirmed re-exposure fails closed and never selects a silent fallback app", async () => {
  const browser = fakeBrowser({
    composerApps: [],
    failConfirm: true,
    menuApps: [...SILENT_FALLBACK_APPS]
  });
  await assert.rejects(
    () => ensureAppExposure(browser, LOCAL_AGENT_BRIDGE_APP),
    /not confirmed|UNAVAILABLE|ABSENT_REPAIRABLE/
  );
  assert.deepEqual(browser.state.selectCalls, [LOCAL_AGENT_BRIDGE_APP]);
  assert.equal(
    browser.state.selectCalls.some((name) => SILENT_FALLBACK_APPS.includes(name)),
    false
  );
});

test("refresh/turn reset does not reuse a previous conversation-global confirmation", async () => {
  const browser = fakeBrowser({ composerApps: [LOCAL_AGENT_BRIDGE_APP] });
  const first = await ensureAppExposure(browser, LOCAL_AGENT_BRIDGE_APP, {
    conversation_id: "same-conversation",
    turn_id: "turn-1"
  });
  assert.equal(first.action, "already_exposed");

  browser.state.composerApps.clear();
  const second = await ensureAppExposure(browser, LOCAL_AGENT_BRIDGE_APP, {
    conversation_id: "same-conversation",
    turn_id: "turn-2"
  });
  assert.equal(second.action, "selected");
  assert.equal(second.state, EXPOSURE_STATE.CONFIRMED);
  assert.deepEqual(browser.state.selectCalls, [LOCAL_AGENT_BRIDGE_APP]);
});

test("mention token is a UI gesture only and is not mixed into prompt hashing", () => {
  const prompt = "Return exactly WEB_EXECUTOR_POC_OK.";
  const token = mentionToken(LOCAL_AGENT_BRIDGE_APP);
  assert.equal(token, "@Local Agent Bridge");
  const logicalHash = createHash("sha256").update(prompt).digest("hex");
  const injectedHash = createHash("sha256").update(token + "\n" + prompt).digest("hex");
  assert.notEqual(logicalHash, injectedHash);
  const summary = summarizeExposure({
    required_apps: [LOCAL_AGENT_BRIDGE_APP],
    results: [
      {
        app: LOCAL_AGENT_BRIDGE_APP,
        state: EXPOSURE_STATE.CONFIRMED,
        action: "selected",
        attempts: 1,
        ui_mention_used: true
      }
    ]
  });
  assert.equal(JSON.stringify(summary).includes(token), false);
  assert.equal(summary.ui_mention_used, true);
});

test("unrelated executions with no required apps skip app selection", async () => {
  const browser = fakeBrowser();
  const apps = resolveRequiredApps({ assignment_id: "LAB-WEX-REASONING" });
  assert.deepEqual(apps, []);
  const summary = await ensureRequiredAppsExposed(browser, apps);
  assert.equal(summary.state, EXPOSURE_STATE.UNKNOWN);
  assert.deepEqual(summary.required_apps, []);
  assert.deepEqual(browser.state.observeCalls, []);
  assert.deepEqual(browser.state.selectCalls, []);
});
