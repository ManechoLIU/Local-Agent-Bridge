import { createHash } from "node:crypto";

import {
  EXPOSURE_STATE,
  assertNotSilentFallback,
  canonicalPromptText,
  classifyObservedExposure,
  uniqueAppNames
} from "./tool-exposure.mjs";

const COMPOSER_SELECTORS = Object.freeze([
  "#prompt-textarea",
  "[data-testid='prompt-textarea']",
  "textarea[placeholder*='Message']",
  "textarea[placeholder*='发送消息']",
  "[contenteditable='true'][data-virtualkeyboard='true']",
  "main [contenteditable='true']"
]);
const COMPOSER_ROOT_SELECTOR = "[data-testid='composer'], form[data-type='unified-composer']";
const APP_CHIP_SELECTOR =
  "[data-testid='composer-mention'],[data-mention-name],[data-app-name],[data-testid='app-mention']";
const MENTION_PICKER_ROOT_SELECTOR =
  "[data-testid='mention-picker'],[data-testid='composer-mentions-menu'],[role='listbox'],[role='menu']";
const MENTION_OPTION_SELECTOR = "[role='option'],[role='menuitem']";

function normalize(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim().toLocaleLowerCase();
}

function sha256(value) {
  return createHash("sha256").update(canonicalPromptText(value)).digest("hex");
}

export function collectComposerAppNames(snapshot) {
  const chips = Array.isArray(snapshot?.chips) ? snapshot.chips : [];
  const names = [];
  for (const chip of chips) {
    if (chip == null) continue;
    if (typeof chip === "string") {
      const name = chip.trim();
      if (name) names.push(name);
      continue;
    }
    const kind = String(chip.kind || "app").toLowerCase();
    if (kind && !["app", "mention", "tool", "connector"].includes(kind)) continue;
    const name = String(chip.name || "").replace(/\s+/g, " ").trim();
    if (name) names.push(name);
  }
  return uniqueAppNames(names);
}

export function pickExactAppOption(appName, options) {
  const wanted = normalize(appName);
  if (!wanted) return null;
  const names = Array.isArray(options)
    ? options.map((value) => String(value || "").trim()).filter(Boolean)
    : [];
  const exact = names.filter((name) => normalize(name) === wanted);
  if (exact.length === 1) return exact[0];
  return null;
}

async function composerLocator(page) {
  if (typeof page?.locator !== "function") return null;
  // Mirror the pinned ChatGPTBrowser.composer()/firstVisible selector order so
  // exposure proof is collected from the same visible composer submitPrompt uses.
  for (const selector of COMPOSER_SELECTORS) {
    const locator = page.locator(selector);
    const candidate = typeof locator?.first === "function" ? locator.first() : locator;
    if (!candidate) continue;
    try {
      const visible =
        typeof candidate.isVisible === "function"
          ? await candidate.isVisible({ timeout: 400 }).catch(() => false)
          : false;
      if (visible) return candidate;
    } catch {
      // Continue through the same semantic fallback order as the pinned browser.
    }
  }
  return null;
}

async function readComposerSnapshot(page) {
  const composer = await composerLocator(page);
  if (!composer || typeof composer.evaluate !== "function") {
    return { scope_verified: false, chips: [] };
  }
  try {
    const snapshot = await composer.evaluate((element, selectors) => {
      const root = element.closest?.(selectors.rootSelector);
      if (!root) return { scope_verified: false, chips: [] };
      const nodes = Array.from(root.querySelectorAll(selectors.chipSelector));
      return {
        scope_verified: true,
        chips: nodes.map((node) => ({
          name: (
            node.getAttribute("data-mention-name") ||
            node.getAttribute("data-app-name") ||
            node.getAttribute("aria-label") ||
            node.textContent ||
            ""
          )
            .replace(/\s+/g, " ")
            .trim(),
          kind: node.getAttribute("data-mention-kind") || "app"
        }))
      };
    }, { rootSelector: COMPOSER_ROOT_SELECTOR, chipSelector: APP_CHIP_SELECTOR });
    return snapshot && typeof snapshot === "object"
      ? { scope_verified: snapshot.scope_verified === true, chips: Array.isArray(snapshot.chips) ? snapshot.chips : [] }
      : { scope_verified: false, chips: [] };
  } catch {
    return { scope_verified: false, chips: [] };
  }
}

async function readComposerLogicalText(page) {
  const composer = await composerLocator(page);
  if (!composer || typeof composer.evaluate !== "function") return null;
  try {
    return await composer.evaluate((element, chipSelector) => {
      if (element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement) {
        return element.value;
      }
      const clone = element.cloneNode(true);
      for (const node of clone.querySelectorAll?.(chipSelector) || []) node.remove();
      return clone.textContent || "";
    }, APP_CHIP_SELECTOR);
  } catch {
    return null;
  }
}

async function promptIntegrity(page, expectedSha256) {
  const expected = String(expectedSha256 || "").trim().toLowerCase();
  if (!expected) return { checked: false, valid: true, observed_sha256: null };
  const logicalText = await readComposerLogicalText(page);
  if (logicalText == null) {
    return { checked: true, valid: false, observed_sha256: null };
  }
  const observed = sha256(logicalText);
  return {
    checked: true,
    valid: observed === expected,
    observed_sha256: observed
  };
}

async function uniqueMentionPickerRoot(page) {
  if (typeof page?.locator !== "function") return null;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    try {
      const roots = page.locator(MENTION_PICKER_ROOT_SELECTOR);
      const count = typeof roots?.count === "function" ? await roots.count() : 0;
      const visible = [];
      for (let index = 0; index < count; index += 1) {
        const root = roots.nth(index);
        const isVisible =
          typeof root?.isVisible === "function" ? await root.isVisible().catch(() => false) : false;
        if (isVisible) visible.push(root);
      }
      if (visible.length === 1) return visible[0];
      if (visible.length > 1) return null;
    } catch {
      return null;
    }
    if (typeof page?.waitForTimeout === "function") {
      await page.waitForTimeout(75);
    }
  }
  return null;
}

async function listMentionOptions(picker) {
  const names = [];
  if (typeof picker?.locator !== "function") return names;
  try {
    const items = picker.locator(MENTION_OPTION_SELECTOR);
    const count = await items.count();
    for (let index = 0; index < count; index += 1) {
      const item = items.nth(index);
      const visible = await item.isVisible().catch(() => false);
      if (!visible) continue;
      const text = String((await item.innerText().catch(() => "")) || "")
        .replace(/\s+/g, " ")
        .trim();
      if (text) names.push(text);
    }
  } catch {
    return [];
  }
  return names;
}

async function exactMentionTarget(picker, appName) {
  if (typeof picker?.getByRole !== "function") return null;
  const candidates = [];
  for (const role of ["option", "menuitem"]) {
    const locator = picker.getByRole(role, { name: appName, exact: true });
    if (!locator) continue;
    try {
      const count = typeof locator.count === "function" ? await locator.count() : 0;
      for (let index = 0; index < count; index += 1) {
        const target = locator.nth(index);
        const visible =
          typeof target?.isVisible === "function" ? await target.isVisible().catch(() => false) : false;
        if (visible) candidates.push(target);
      }
    } catch {
      return null;
    }
  }
  return candidates.length === 1 ? candidates[0] : null;
}

function exposureResult(appName, observedNames, extra = {}) {
  const observed_apps = uniqueAppNames(observedNames);
  const state =
    extra.state ||
    classifyObservedExposure({
      required: appName,
      observed: observed_apps,
      available: extra.available
    });
  return {
    app: appName,
    observed_apps,
    state,
    exposed: state === EXPOSURE_STATE.CONFIRMED,
    confirmed: state === EXPOSURE_STATE.CONFIRMED,
    ui_mention_used: extra.ui_mention_used === true,
    action: extra.action || null,
    attempts: extra.attempts || 0,
    prompt_integrity: extra.prompt_integrity ?? null
  };
}

export async function observeChatGptAppExposure(browser, appName, options = {}) {
  const app = String(appName || "").trim();
  if (!app) throw new Error("app name is required");
  if (typeof browser?.page !== "function") {
    return exposureResult(app, [], { state: EXPOSURE_STATE.UNAVAILABLE, available: false });
  }
  const page = await browser.page();
  const integrity = await promptIntegrity(page, options.expected_prompt_sha256);
  if (!integrity.valid) {
    return exposureResult(app, [], {
      state: EXPOSURE_STATE.UNAVAILABLE,
      available: false,
      action: "prompt_integrity_failed",
      prompt_integrity: false
    });
  }
  const snapshot = await readComposerSnapshot(page);
  if (snapshot.scope_verified !== true) {
    return exposureResult(app, [], {
      state: EXPOSURE_STATE.UNAVAILABLE,
      available: false,
      action: "composer_scope_unverified",
      prompt_integrity: integrity.checked ? true : null
    });
  }
  const names = collectComposerAppNames(snapshot);
  return exposureResult(app, names, {
    prompt_integrity: integrity.checked ? true : null
  });
}

export async function selectChatGptApp(browser, appName, options = {}) {
  const app = String(appName || "").trim();
  if (!app) throw new Error("app name is required");
  if (typeof browser?.page !== "function") {
    return exposureResult(app, [], { state: EXPOSURE_STATE.UNAVAILABLE, available: false });
  }
  const page = await browser.page();
  const before = await promptIntegrity(page, options.expected_prompt_sha256);
  if (!before.valid) {
    return exposureResult(app, [], {
      state: EXPOSURE_STATE.UNAVAILABLE,
      available: false,
      action: "prompt_integrity_failed",
      prompt_integrity: false
    });
  }

  const composer = await composerLocator(page);
  if (composer?.click) await composer.click();
  if (page.keyboard?.type) await page.keyboard.type("@");

  const picker = await uniqueMentionPickerRoot(page);
  if (!picker) {
    return exposureResult(app, collectComposerAppNames(await readComposerSnapshot(page)), {
      state: EXPOSURE_STATE.UNAVAILABLE,
      available: false,
      action: "picker_scope_unverified",
      prompt_integrity: before.checked ? true : null
    });
  }

  const names = await listMentionOptions(picker);
  const exact = pickExactAppOption(app, names);
  if (!exact) {
    return exposureResult(app, collectComposerAppNames(await readComposerSnapshot(page)), {
      state: EXPOSURE_STATE.UNAVAILABLE,
      available: false,
      action: "picker_unavailable",
      prompt_integrity: before.checked ? true : null
    });
  }
  assertNotSilentFallback(app, exact);

  const target = await exactMentionTarget(picker, exact);
  if (!target) {
    return exposureResult(app, collectComposerAppNames(await readComposerSnapshot(page)), {
      state: EXPOSURE_STATE.UNAVAILABLE,
      available: false,
      action: "picker_not_unique",
      prompt_integrity: before.checked ? true : null
    });
  }
  if (typeof browser.click === "function") {
    await browser.click(target, "wex-select-app");
  } else if (typeof target.click === "function") {
    await target.click();
  }

  const after = await promptIntegrity(page, options.expected_prompt_sha256);
  if (!after.valid) {
    return exposureResult(app, collectComposerAppNames(await readComposerSnapshot(page)), {
      state: EXPOSURE_STATE.UNAVAILABLE,
      available: false,
      ui_mention_used: true,
      action: "prompt_integrity_failed",
      attempts: 1,
      prompt_integrity: false
    });
  }

  const observedNames = collectComposerAppNames(await readComposerSnapshot(page));
  const confirmed = observedNames.some((name) => normalize(name) === normalize(app));
  return exposureResult(app, observedNames, {
    state: confirmed ? EXPOSURE_STATE.CONFIRMED : EXPOSURE_STATE.ABSENT_REPAIRABLE,
    ui_mention_used: true,
    action: confirmed ? "selected" : "failed",
    attempts: 1,
    prompt_integrity: after.checked ? true : null
  });
}

export async function ensureChatGptAppExposure(browser, appName, options = {}) {
  const expectedPromptSha256 = String(options?.expected_prompt_sha256 || "").trim().toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(expectedPromptSha256)) {
    const error = new Error("required Web tool app needs an expected prompt SHA-256 integrity fence");
    error.exposure = exposureResult(String(appName || "").trim(), [], {
      state: EXPOSURE_STATE.UNAVAILABLE,
      available: false,
      action: "prompt_integrity_unavailable",
      prompt_integrity: false
    });
    throw error;
  }
  const observed = await observeChatGptAppExposure(browser, appName, {
    ...options,
    expected_prompt_sha256: expectedPromptSha256
  });
  if (observed.state === EXPOSURE_STATE.CONFIRMED) {
    return { ...observed, action: "already_exposed", attempts: 0 };
  }
  if (observed.action === "prompt_integrity_failed") {
    const error = new Error("composer prompt integrity check failed before app exposure");
    error.exposure = { ...observed, action: "failed", attempts: 0 };
    throw error;
  }
  const selected = await selectChatGptApp(browser, appName, { ...options, expected_prompt_sha256: expectedPromptSha256 });
  if (selected.state !== EXPOSURE_STATE.CONFIRMED) {
    const message =
      selected.action === "prompt_integrity_failed"
        ? "composer prompt integrity check failed during app exposure"
        : "required Web tool app is not uniquely available in current ChatGPT UI: " +
          appName +
          " (" +
          selected.state +
          ")";
    const error = new Error(message);
    error.exposure = { ...selected, action: "failed", attempts: 1 };
    throw error;
  }
  const confirmed = await observeChatGptAppExposure(browser, appName, { ...options, expected_prompt_sha256: expectedPromptSha256 });
  if (confirmed.state !== EXPOSURE_STATE.CONFIRMED) {
    const error = new Error(
      confirmed.action === "prompt_integrity_failed"
        ? "composer prompt integrity check failed after app exposure"
        : "required Web tool app is not confirmed for this message: " +
          appName +
          " (" +
          confirmed.state +
          ")"
    );
    error.exposure = { ...confirmed, action: "failed", attempts: 1 };
    throw error;
  }
  return {
    ...confirmed,
    action: "selected",
    attempts: 1,
    ui_mention_used: selected.ui_mention_used === true
  };
}

export function attachAppExposureMethods(browser) {
  if (!browser || typeof browser !== "object") return browser;
  if (typeof browser.ensureAppExposure === "function") return browser;
  browser.observeAppExposure = (appName, options) =>
    observeChatGptAppExposure(browser, appName, options);
  browser.selectApp = (appName, options) => selectChatGptApp(browser, appName, options);
  browser.ensureAppExposure = (appName, options) =>
    ensureChatGptAppExposure(browser, appName, options);
  return browser;
}
