import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  attachAppExposureMethods,
  collectComposerAppNames,
  ensureChatGptAppExposure,
  observeChatGptAppExposure,
  pickExactAppOption,
  selectChatGptApp
} from "./chatgpt-app-exposure.mjs";
import { EXPOSURE_STATE, LOCAL_AGENT_BRIDGE_APP, SILENT_FALLBACK_APPS } from "./tool-exposure.mjs";

function normalized(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim().toLocaleLowerCase();
}

function stubPage({
  composerChips = [],
  globalChips = [],
  options = [],
  outsideOptions = [],
  composerText = "",
  composerScopeVerified = true,
  composerRootKind = "testid",
  pickerPresent = true,
  selectVisible = true,
  consumeMentionTrigger = true
} = {}) {
  const clicks = [];
  const typed = [];
  const currentComposerChips = [...composerChips];
  const pickerOptions = [...options];
  const page = {
    globalChips: [...globalChips],
    outsideOptions: [...outsideOptions],
    clicks,
    typed,
    keyboard: {
      async type(text) {
        typed.push(text);
        composerText += text;
      },
      async press(key) {
        typed.push(key);
      }
    },
    async evaluate() {
      return { chips: [...page.globalChips] };
    },
    locator(selector) {
      if (selector.includes("prompt-textarea")) {
        const composer = {
          selector,
          last() {
            return composer;
          },
          async click() {
            clicks.push(selector);
          },
          async isVisible() {
            return true;
          },
          async evaluate(fn, arg) {
            const body = String(fn);
            if (body.includes("scope_verified")) {
              let verified = composerScopeVerified;
              if (verified && composerRootKind === "unified-form") {
                const rootSelector = arg && typeof arg === "object" ? String(arg.rootSelector || "") : "";
                verified = rootSelector.includes("form[data-type='unified-composer']");
              }
              return {
                scope_verified: verified,
                chips: verified ? [...currentComposerChips] : []
              };
            }
            return composerText;
          }
        };
        return composer;
      }

      if (selector.includes("listbox") || selector.includes("mention-picker") || selector.includes("composer-mentions-menu") || selector.includes("[role='menu']")) {
        const makeOption = (candidate) => ({
          async isVisible() {
            return Boolean(candidate) && selectVisible;
          },
          async innerText() {
            return candidate || "";
          },
          async click() {
            clicks.push(candidate);
            if (normalized(candidate) === normalized(LOCAL_AGENT_BRIDGE_APP)) {
              if (consumeMentionTrigger && composerText.endsWith("@")) {
                composerText = composerText.slice(0, -1);
              }
              currentComposerChips.push({ name: LOCAL_AGENT_BRIDGE_APP, kind: "app" });
              page.globalChips.push({ name: LOCAL_AGENT_BRIDGE_APP, kind: "app" });
            }
          }
        });
        const picker = {
          async isVisible() {
            return pickerPresent;
          },
          locator(optionSelector) {
            const values = optionSelector.includes("option") || optionSelector.includes("menuitem")
              ? pickerOptions
              : [];
            return {
              async count() { return values.length; },
              nth(index) { return makeOption(values[index]); }
            };
          },
          getByRole(role, { name, exact = false } = {}) {
            const matches = role === "option"
              ? pickerOptions.filter((candidate) => {
                  if (exact) return normalized(candidate) === normalized(name);
                  return normalized(candidate).includes(normalized(name));
                })
              : [];
            return {
              async count() { return matches.length; },
              nth(index) { return makeOption(matches[index]); }
            };
          }
        };
        return {
          async count() { return pickerPresent ? 1 : 0; },
          nth() { return picker; }
        };
      }

      return {
        async count() { return 0; },
        nth() { return null; }
      };
    },
    getByRole(role, { name, exact = false } = {}) {
      const matches = page.outsideOptions.filter((candidate) => {
        if (exact) return normalized(candidate) === normalized(name);
        return normalized(candidate).includes(normalized(name));
      });
      return {
        async count() { return matches.length; },
        nth(index) {
          const candidate = matches[index];
          return {
            async isVisible() { return Boolean(candidate); },
            async click() { clicks.push("OUTSIDE:" + candidate); }
          };
        }
      };
    },
    get composerText() { return composerText; },
    get composerChips() { return [...currentComposerChips]; }
  };
  return page;
}

function stubBrowser(page) {
  return {
    async page() {
      return page;
    },
    async click(locator, name) {
      this.lastClick = name;
      if (locator?.click) await locator.click();
    }
  };
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

test("collectComposerAppNames reads chip names without treating DOM as Host identity", () => {
  assert.deepEqual(
    collectComposerAppNames({
      chips: [
        { name: "Local Agent Bridge", kind: "app" },
        { name: "GPT-5.6 Sol", kind: "model" }
      ]
    }),
    ["Local Agent Bridge"]
  );
});

test("pickExactAppOption never falls back to AI-Bridge or Identity Probe", () => {
  assert.equal(
    pickExactAppOption(LOCAL_AGENT_BRIDGE_APP, [
      "AI-Bridge",
      "Local Bridge Identity Probe",
      "Local Agent Bridge"
    ]),
    LOCAL_AGENT_BRIDGE_APP
  );
  assert.equal(pickExactAppOption(LOCAL_AGENT_BRIDGE_APP, SILENT_FALLBACK_APPS), null);
});

test("document-global stale LAB nodes do not confirm current-composer exposure", async () => {
  const page = stubPage({
    composerChips: [],
    globalChips: [{ name: LOCAL_AGENT_BRIDGE_APP, kind: "app" }]
  });
  const observed = await observeChatGptAppExposure(stubBrowser(page), LOCAL_AGENT_BRIDGE_APP);
  assert.notEqual(observed.state, EXPOSURE_STATE.CONFIRMED);
  assert.equal(observed.confirmed, false);
});

test("ChatGPT adapter observes a selected current-composer app as CONFIRMED", async () => {
  const page = stubPage({
    composerChips: [{ name: LOCAL_AGENT_BRIDGE_APP, kind: "app" }]
  });
  const observed = await observeChatGptAppExposure(stubBrowser(page), LOCAL_AGENT_BRIDGE_APP);
  assert.equal(observed.state, EXPOSURE_STATE.CONFIRMED);
  assert.equal(observed.exposed, true);
  assert.equal(observed.host_identity, undefined);
});




function composerNode({ visible, chips = [], text = "" }) {
  return {
    async isVisible() { return visible; },
    async evaluate(fn) {
      const body = String(fn);
      if (body.includes("scope_verified")) {
        return { scope_verified: true, chips };
      }
      return text;
    },
    async click() {}
  };
}

function composerResolutionPage({ stalePrimary = null, currentFallback = null } = {}) {
  function collection(node) {
    return {
      first() { return node; },
      last() { return node; },
      async count() { return node ? 1 : 0; }
    };
  }
  return {
    locator(selector) {
      const value = String(selector);
      // Current implementation queries the two primary selectors as one combined locator.
      if (value.includes("#prompt-textarea") && value.includes("data-testid='prompt-textarea'")) {
        return collection(stalePrimary);
      }
      if (value === "#prompt-textarea" || value === "[data-testid='prompt-textarea']") {
        return collection(stalePrimary);
      }
      if (value === "textarea[placeholder*='Message']") {
        return collection(currentFallback);
      }
      return collection(null);
    }
  };
}

test("hidden stale primary composer cannot prove exposure for visible fallback composer", async () => {
  const prompt = "Return exactly WEB_EXECUTOR_POC_OK.";
  const page = composerResolutionPage({
    stalePrimary: composerNode({
      visible: false,
      text: prompt,
      chips: [{ name: LOCAL_AGENT_BRIDGE_APP, kind: "app" }]
    }),
    currentFallback: composerNode({ visible: true, text: prompt, chips: [] })
  });
  const observed = await observeChatGptAppExposure(stubBrowser(page), LOCAL_AGENT_BRIDGE_APP, {
    expected_prompt_sha256: sha256(prompt)
  });
  assert.notEqual(observed.state, EXPOSURE_STATE.CONFIRMED);
});

test("pinned browser fallback composer can confirm current app exposure", async () => {
  const prompt = "Return exactly WEB_EXECUTOR_POC_OK.";
  const page = composerResolutionPage({
    stalePrimary: null,
    currentFallback: composerNode({
      visible: true,
      text: prompt,
      chips: [{ name: LOCAL_AGENT_BRIDGE_APP, kind: "app" }]
    })
  });
  const observed = await observeChatGptAppExposure(stubBrowser(page), LOCAL_AGENT_BRIDGE_APP, {
    expected_prompt_sha256: sha256(prompt)
  });
  assert.equal(observed.state, EXPOSURE_STATE.CONFIRMED);
});

test("required app ensure fails closed when expected prompt hash is missing", async () => {
  const prompt = "Return exactly WEB_EXECUTOR_POC_OK.";
  const page = stubPage({
    composerChips: [{ name: LOCAL_AGENT_BRIDGE_APP, kind: "app" }],
    composerText: prompt
  });
  await assert.rejects(
    () => ensureChatGptAppExposure(stubBrowser(page), LOCAL_AGENT_BRIDGE_APP),
    /prompt.*hash|prompt.*integrity|required.*sha/i
  );
});

test("standard unified-composer form is accepted as the current composer scope", async () => {
  const page = stubPage({
    composerChips: [{ name: LOCAL_AGENT_BRIDGE_APP, kind: "app" }],
    composerRootKind: "unified-form"
  });
  const observed = await observeChatGptAppExposure(stubBrowser(page), LOCAL_AGENT_BRIDGE_APP);
  assert.equal(observed.state, EXPOSURE_STATE.CONFIRMED);
  assert.equal(observed.confirmed, true);
});

test("missing trusted composer root never confirms from transcript or stale snapshot", async () => {
  const page = stubPage({
    composerChips: [{ name: LOCAL_AGENT_BRIDGE_APP, kind: "app" }],
    globalChips: [{ name: LOCAL_AGENT_BRIDGE_APP, kind: "app" }],
    composerText: "Return exactly WEB_EXECUTOR_POC_OK.",
    composerScopeVerified: false
  });
  const observed = await observeChatGptAppExposure(stubBrowser(page), LOCAL_AGENT_BRIDGE_APP, {
    expected_prompt_sha256: sha256("Return exactly WEB_EXECUTOR_POC_OK.")
  });
  assert.notEqual(observed.state, EXPOSURE_STATE.CONFIRMED);
});

test("empty mention picker fails closed without synthesizing an app option", async () => {
  const page = stubPage({ composerChips: [], options: [] });
  const selected = await selectChatGptApp(stubBrowser(page), LOCAL_AGENT_BRIDGE_APP);
  assert.equal(selected.state, EXPOSURE_STATE.UNAVAILABLE);
  assert.deepEqual(page.clicks.filter((value) => value === LOCAL_AGENT_BRIDGE_APP), []);
});

test("page-global exact option outside current mention picker is ignored", async () => {
  const prompt = "Return exactly WEB_EXECUTOR_POC_OK.";
  const page = stubPage({
    composerChips: [],
    options: [],
    outsideOptions: [LOCAL_AGENT_BRIDGE_APP],
    composerText: prompt,
    pickerPresent: true
  });
  const selected = await selectChatGptApp(stubBrowser(page), LOCAL_AGENT_BRIDGE_APP, {
    expected_prompt_sha256: sha256(prompt)
  });
  assert.notEqual(selected.state, EXPOSURE_STATE.CONFIRMED);
  assert.equal(page.clicks.some((value) => String(value).startsWith("OUTSIDE:")), false);
});

test("exact unique app selection cannot click a substring sibling", async () => {
  const page = stubPage({
    composerChips: [],
    options: ["Local Agent Bridge Legacy", LOCAL_AGENT_BRIDGE_APP]
  });
  const selected = await selectChatGptApp(stubBrowser(page), LOCAL_AGENT_BRIDGE_APP);
  assert.equal(selected.state, EXPOSURE_STATE.CONFIRMED);
  assert.deepEqual(page.clicks.filter((value) => /Local Agent Bridge/.test(String(value))), [
    LOCAL_AGENT_BRIDGE_APP
  ]);
});

test("ChatGPT adapter selects an absent app through the exact mention picker", async () => {
  const page = stubPage({
    composerChips: [],
    options: [LOCAL_AGENT_BRIDGE_APP, ...SILENT_FALLBACK_APPS]
  });
  const browser = stubBrowser(page);
  const selected = await selectChatGptApp(browser, LOCAL_AGENT_BRIDGE_APP);
  assert.equal(selected.state, EXPOSURE_STATE.CONFIRMED);
  assert.equal(page.clicks.includes(LOCAL_AGENT_BRIDGE_APP), true);
  assert.equal(page.clicks.includes("AI-Bridge"), false);
});

test("prompt integrity is rechecked after the @ app-selection gesture", async () => {
  const prompt = "Return exactly WEB_EXECUTOR_POC_OK.";
  const page = stubPage({
    composerChips: [],
    options: [LOCAL_AGENT_BRIDGE_APP],
    composerText: prompt,
    consumeMentionTrigger: false
  });
  await assert.rejects(
    () =>
      ensureChatGptAppExposure(stubBrowser(page), LOCAL_AGENT_BRIDGE_APP, {
        expected_prompt_sha256: sha256(prompt)
      }),
    /prompt.*integrity|not confirmed|UNAVAILABLE/i
  );
});

test("prompt integrity stays valid when the UI consumes the mention trigger into a chip", async () => {
  const prompt = "Return exactly WEB_EXECUTOR_POC_OK.";
  const page = stubPage({
    composerChips: [],
    options: [LOCAL_AGENT_BRIDGE_APP],
    composerText: prompt,
    consumeMentionTrigger: true
  });
  const selected = await ensureChatGptAppExposure(stubBrowser(page), LOCAL_AGENT_BRIDGE_APP, {
    expected_prompt_sha256: sha256(prompt)
  });
  assert.equal(selected.state, EXPOSURE_STATE.CONFIRMED);
  assert.equal(page.composerText, prompt);
});

test("ChatGPT adapter fail-closes when the required app cannot be confirmed", async () => {
  const prompt = "Return exactly WEB_EXECUTOR_POC_OK.";
  const page = stubPage({
    composerChips: [],
    options: [...SILENT_FALLBACK_APPS],
    composerText: prompt,
    selectVisible: true
  });
  await assert.rejects(
    () => ensureChatGptAppExposure(stubBrowser(page), LOCAL_AGENT_BRIDGE_APP, {
      expected_prompt_sha256: sha256(prompt)
    }),
    /not confirmed|UNAVAILABLE|ABSENT_REPAIRABLE|not uniquely available|prompt.*integrity/
  );
});

test("attachAppExposureMethods does not overwrite an existing adapter method", () => {
  const browser = {
    async ensureAppExposure() {
      return { state: EXPOSURE_STATE.CONFIRMED, sentinel: true };
    }
  };
  attachAppExposureMethods(browser);
  return browser.ensureAppExposure().then((result) => {
    assert.equal(result.sentinel, true);
  });
});
