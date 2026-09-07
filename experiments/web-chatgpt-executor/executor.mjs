import { createHash, randomUUID } from "node:crypto";
import { appendFile, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
export const DEFAULT_STATE_DIR = resolve(here, "state");

function nowIso() {
  return new Date().toISOString();
}

function normalize(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim().toLocaleLowerCase();
}

function hash16(value) {
  return createHash("sha256").update(String(value)).digest("hex").slice(0, 16);
}

export function stableConversationId(value) {
  const text = String(value || "").trim();
  if (!/^[A-Za-z0-9-]{20,}$/.test(text)) return null;
  if (/^(?:web|new|chat|conversation|pending)$/i.test(text)) return null;
  return text;
}

async function atomicWriteJson(path, value) {
  await mkdir(dirname(path), { recursive: true });
  const temporary = path + "." + process.pid + "." + randomUUID() + ".tmp";
  await writeFile(temporary, JSON.stringify(value, null, 2) + "\n", {
    encoding: "utf8",
    mode: 0o600
  });
  await rename(temporary, path);
}

async function appendJsonLine(path, value) {
  await mkdir(dirname(path), { recursive: true });
  await appendFile(path, JSON.stringify(value) + "\n", {
    encoding: "utf8",
    mode: 0o600
  });
}

function executionPath(stateDir, executionId) {
  return resolve(stateDir, "executions", executionId + ".json");
}

function resultPath(stateDir, executionId) {
  return resolve(stateDir, "results", executionId + ".txt");
}

function eventsPath(stateDir) {
  return resolve(stateDir, "events.jsonl");
}

async function readJson(path) {
  return JSON.parse(await readFile(path, "utf8"));
}

async function loadBrowserSessionSnapshot(configModule) {
  const file = configModule?.BROWSER_STATE_FILE;
  if (!file) return { browser_session_id: null, browser_session_attestation: "unavailable" };
  try {
    const state = JSON.parse(await readFile(file, "utf8"));
    const startedAt = Number(state?.startedAt || 0);
    const pid = Number(state?.pid || 0);
    const port = Number(state?.port || 0);
    if (!startedAt) {
      return { browser_session_id: null, browser_session_attestation: "unavailable" };
    }
    return {
      browser_session_id: "bwex_" + hash16(String(startedAt) + ":" + String(pid) + ":" + String(port)),
      browser_session_attestation: "adapter_observed"
    };
  } catch {
    return { browser_session_id: null, browser_session_attestation: "unavailable" };
  }
}

export function selectPersonalChatMode(modeListing) {
  const modes = Array.isArray(modeListing?.modes) ? modeListing.modes : [];
  const preferred = modes.find((item) => /^(聊天|chat)$/i.test(normalize(item?.name)));
  if (preferred?.name) return preferred.name;
  const nonWork = modes.filter((item) => !/(工作|work)/i.test(normalize(item?.name)));
  if (nonWork.length === 1 && nonWork[0]?.name) return nonWork[0].name;
  throw new Error("personal Chat mode cannot be uniquely verified from the current ChatGPT Web UI");
}

export function chooseRequestedModel(requested, available) {
  const requestedValue = String(requested || "").trim();
  const names = Array.isArray(available)
    ? available.map((value) => String(value || "").trim()).filter(Boolean)
    : [];
  if (!requestedValue) return null;
  const exact = names.filter((name) => normalize(name) === normalize(requestedValue));
  if (exact.length === 1) return exact[0];
  const partial = names.filter(
    (name) =>
      normalize(name).includes(normalize(requestedValue)) ||
      normalize(requestedValue).includes(normalize(name))
  );
  if (partial.length === 1) return partial[0];
  throw new Error(
    "requested Web model is not uniquely available in current ChatGPT UI: " + requestedValue
  );
}

async function composerPowerButton(browser) {
  const page = await browser.page();
  const semantic = page.locator("button.__composer-pill:visible").last();
  if (await semantic.isVisible({ timeout: 1000 }).catch(() => false)) return semantic;
  const fallback = page
    .locator("button:visible")
    .filter({ hasText: /^(?:High|Very High|Standard|Fast|高|极高|标准|快速)$/i })
    .last();
  if (await fallback.isVisible({ timeout: 1000 }).catch(() => false)) return fallback;
  throw new Error("ChatGPT model/power control is not visible in ordinary Chat mode");
}

async function readVisibleModelMenu(browser, { leaveOpen = false } = {}) {
  const page = await browser.page();
  const power = await composerPowerButton(browser);
  await browser.click(power, "wex-open-model-menu");
  await page.waitForTimeout(200);
  const radios = page.getByRole("menuitemradio");
  const count = await radios.count();
  const items = [];
  for (let index = 0; index < count; index += 1) {
    const item = radios.nth(index);
    if (!(await item.isVisible().catch(() => false))) continue;
    const name = (await item.innerText().catch(() => "")).replace(/\s+/g, " ").trim();
    if (!name) continue;
    items.push({
      name,
      checked: (await item.getAttribute("aria-checked")) === "true",
      locator: item
    });
  }
  if (!items.length) {
    await page.keyboard.press("Escape").catch(() => {});
    throw new Error("ChatGPT model menu opened but exposed no model choices");
  }
  if (!leaveOpen) await page.keyboard.press("Escape").catch(() => {});
  return items;
}

export async function routeWebModel(browser, requested) {
  let items = await readVisibleModelMenu(browser, { leaveOpen: Boolean(requested) });
  const available = items.map((item) => item.name);
  if (!requested) {
    const selected = items.find((item) => item.checked)?.name || null;
    return { available, selected, changed: false };
  }

  const selectedName = chooseRequestedModel(requested, available);
  const target = items.find((item) => item.name === selectedName);
  if (!target) throw new Error("requested Web model disappeared from current UI");
  const alreadySelected = target.checked;
  if (!alreadySelected) {
    await browser.click(target.locator, "wex-select-model");
    const page = await browser.page();
    await page.waitForTimeout(250);
  } else {
    const page = await browser.page();
    await page.keyboard.press("Escape").catch(() => {});
  }

  items = await readVisibleModelMenu(browser, { leaveOpen: false });
  const verified = items.find((item) => item.checked)?.name || null;
  if (normalize(verified) !== normalize(selectedName)) {
    throw new Error(
      "Web model selection failed verification: requested " +
        selectedName +
        ", observed " +
        String(verified)
    );
  }
  return { available: items.map((item) => item.name), selected: verified, changed: !alreadySelected };
}

export function reasoningEffortIndex(value) {
  const normalized = normalize(value);
  if (!normalized) return null;
  if (["low", "minimal", "min"].includes(normalized)) return 0;
  if (["medium", "normal", "standard", "med"].includes(normalized)) return 1;
  if (["high", "xhigh", "extra high", "max", "maximum"].includes(normalized)) return 2;
  throw new Error("unsupported Web reasoning effort: " + String(value));
}

export async function routeWebReasoningEffort(browser, requested) {
  const expected = reasoningEffortIndex(requested);
  if (expected == null) {
    return {
      requested: null,
      observed_index: null,
      observed_label: null,
      attestation: "unavailable",
      changed: false
    };
  }
  const page = await browser.page();
  const powerButton = await composerPowerButton(browser);
  await browser.click(powerButton, "wex-open-reasoning-menu");
  await page.waitForTimeout(150);
  const power = page.getByRole("menuitem", { name: "Power" }).last();
  if (!(await power.isVisible().catch(() => false))) {
    await page.keyboard.press("Escape").catch(() => {});
    throw new Error("ChatGPT reasoning effort control is unavailable");
  }
  const slider = power.locator("[role=slider]").first();
  const current = Number(await slider.getAttribute("aria-valuenow"));
  const min = Number(await slider.getAttribute("aria-valuemin"));
  const max = Number(await slider.getAttribute("aria-valuemax"));
  if (![current, min, max].every(Number.isFinite) || min !== 0 || max !== 2) {
    await page.keyboard.press("Escape").catch(() => {});
    throw new Error("unexpected ChatGPT reasoning effort slider range");
  }
  let observed = current;
  const key = expected < observed ? "ArrowLeft" : "ArrowRight";
  while (observed !== expected) {
    await browser.press(power, key, "wex-select-reasoning-effort");
    await page.waitForTimeout(120);
    observed = Number(await slider.getAttribute("aria-valuenow"));
    if (!Number.isFinite(observed)) {
      await page.keyboard.press("Escape").catch(() => {});
      throw new Error("reasoning effort slider lost its numeric value");
    }
  }
  await page.keyboard.press("Escape").catch(() => {});
  const labels = ["Low", "Medium", "High"];
  return {
    requested: String(requested),
    observed_index: observed,
    observed_label: labels[observed] || String(observed),
    attestation: "partial",
    changed: observed !== current
  };
}

export function classifyModelAttestation(requested, observed) {
  const requestedValue = String(requested || "").trim() || null;
  const observedValue = String(observed || "").trim() || null;
  if (!observedValue) {
    return {
      model_requested: requestedValue,
      model_observed: null,
      model_attestation: "unavailable",
      model_match: null
    };
  }
  return {
    model_requested: requestedValue,
    model_observed: observedValue,
    model_attestation: "partial",
    model_match: requestedValue ? normalize(requestedValue) === normalize(observedValue) : null
  };
}

function sanitizedError(error) {
  return {
    name: error instanceof Error ? error.name : "Error",
    message: error instanceof Error ? error.message : String(error)
  };
}

async function persistRecord(stateDir, record) {
  await atomicWriteJson(executionPath(stateDir, record.execution_id), record);
}

async function emitEvent(stateDir, record, event, extra = {}) {
  const payload = {
    schema_version: 1,
    record_kind: "web_chatgpt_executor_event",
    event_id: randomUUID(),
    event,
    observed_at: nowIso(),
    assignment_id: record.assignment_id,
    execution_id: record.execution_id,
    attempt: record.attempt,
    browser_session_id: record.browser_session_id ?? null,
    conversation_id: record.conversation_id ?? null,
    provider: "chatgpt_web",
    provenance: "local_web_executor_adapter",
    ...extra
  };
  await appendJsonLine(eventsPath(stateDir), payload);
  return payload;
}

export async function prepareExecution(input, { stateDir = DEFAULT_STATE_DIR } = {}) {
  const assignmentId = String(input?.assignment_id || "").trim();
  const prompt = String(input?.prompt || "");
  if (!assignmentId) throw new Error("assignment_id is required");
  if (!prompt.trim()) throw new Error("prompt is required");
  const attempt = Number(input?.attempt || 1);
  if (!Number.isInteger(attempt) || attempt < 1) throw new Error("attempt must be a positive integer");

  const createdAt = nowIso();
  const record = {
    schema_version: 1,
    record_kind: "web_chatgpt_execution",
    assignment_id: assignmentId,
    task_id: String(input?.task_id || "").trim() || null,
    execution_id: "wex_" + randomUUID(),
    attempt,
    provider_requested: "chatgpt_web",
    provider_observed: null,
    model_requested: String(input?.model || "").trim() || null,
    model_observed: null,
    model_attestation: "unavailable",
    model_match: null,
    reasoning_effort_requested: String(input?.reasoning_effort || "").trim() || null,
    reasoning_effort_observed: null,
    reasoning_effort_attestation: "unavailable",
    mode_requested: "personal_chat",
    mode_observed: null,
    browser_session_id: null,
    browser_session_attestation: "unavailable",
    conversation_id: null,
    conversation_attestation: "unavailable",
    status: "queued",
    prompt_sha256: createHash("sha256").update(prompt).digest("hex"),
    prompt_length: Buffer.byteLength(prompt),
    created_at: createdAt,
    started_at: null,
    updated_at: createdAt,
    terminal_at: null,
    result_locator: null,
    terminal_receipt: null,
    recovery_generation: 0,
    provenance: "local_web_executor_adapter"
  };
  await persistRecord(stateDir, record);
  await emitEvent(stateDir, record, "queued");
  return record;
}

async function defaultBrowserModules() {
  const modules = await Promise.all([
    import("chatgpt-web-mcp/src/browser.js"),
    import("chatgpt-web-mcp/src/config.js")
  ]);
  return {
    createBrowser: () => new modules[0].ChatGPTBrowser(),
    config: modules[1]
  };
}

async function pollUntilTerminal({ browser, record, stateDir, timeoutMs, pollIntervalMs, config }) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    last = await browser.runExclusive(
      () => browser.getLatestResponse(),
      { name: "wex-poll-" + record.execution_id }
    );
    const session = await loadBrowserSessionSnapshot(config);
    if (session.browser_session_id && session.browser_session_id !== record.browser_session_id) {
      record.browser_session_id = session.browser_session_id;
      record.browser_session_attestation = session.browser_session_attestation;
    }
    const observedConversation = stableConversationId(last?.conversationId);
    if (observedConversation && observedConversation !== record.conversation_id) {
      record.conversation_id = observedConversation;
      record.conversation_attestation = "ui_observed";
      await emitEvent(stateDir, record, "conversation_bound", {
        conversation_attestation: "ui_observed"
      });
    }
    record.updated_at = nowIso();
    await persistRecord(stateDir, record);

    if (
      stableConversationId(last?.conversationId) &&
      last?.response &&
      last?.generating === false &&
      Number(last?.assistantMessageCount || 0) > 0
    ) {
      return last;
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, pollIntervalMs));
  }
  const error = new Error("web execution timed out after " + timeoutMs + "ms");
  error.partial = last;
  throw error;
}

export async function executePrepared(
  record,
  input,
  {
    stateDir = DEFAULT_STATE_DIR,
    browserModules = null,
    modelRouter = routeWebModel,
    reasoningRouter = routeWebReasoningEffort,
    timeoutMs = 180000,
    pollIntervalMs = 2000
  } = {}
) {
  const modules = browserModules || (await defaultBrowserModules());
  const browser = modules.createBrowser();
  const prompt = String(input?.prompt || "");
  if (!prompt.trim()) throw new Error("prompt is required");

  try {
    const dispatched = await browser.runExclusive(async () => {
      await browser.newChat({ temporary: false }, { includeStatus: false });
      const modes = await browser.listModes();
      const personalMode = selectPersonalChatMode(modes);
      if (normalize(modes.current) !== normalize(personalMode)) {
        await browser.selectMode(personalMode);
      }
      const modelRoute = await modelRouter(browser, record.model_requested);
      const reasoningRoute = await reasoningRouter(browser, record.reasoning_effort_requested);
      const sent = await browser.sendMessage({
        prompt,
        newChat: false,
        temporary: false,
        wait: false
      });
      return { personalMode, modelRoute, reasoningRoute, sent };
    }, { name: "wex-dispatch-" + record.execution_id });

    const session = await loadBrowserSessionSnapshot(modules.config);
    record.provider_observed = "chatgpt_web";
    record.mode_observed = dispatched.personalMode;
    record.status = "running";
    record.started_at = nowIso();
    record.updated_at = record.started_at;
    record.browser_session_id = session.browser_session_id;
    record.browser_session_attestation = session.browser_session_attestation;
    record.conversation_id = stableConversationId(dispatched.sent?.conversationId);
    record.conversation_attestation = record.conversation_id ? "ui_observed" : "pending";
    await persistRecord(stateDir, record);
    await emitEvent(stateDir, record, "started", {
      dispatch_attestation: "adapter_observed"
    });

    const terminal = await pollUntilTerminal({
      browser,
      record,
      stateDir,
      timeoutMs,
      pollIntervalMs,
      config: modules.config
    });

    const modelFacts = classifyModelAttestation(
      record.model_requested,
      dispatched.modelRoute?.selected || terminal?.model || dispatched.sent?.model || null
    );
    Object.assign(record, modelFacts);
    record.reasoning_effort_observed = dispatched.reasoningRoute?.observed_label || null;
    record.reasoning_effort_attestation =
      dispatched.reasoningRoute?.attestation || "unavailable";
    record.status = "completed";
    record.updated_at = nowIso();
    record.terminal_at = record.updated_at;
    record.conversation_id =
      stableConversationId(terminal.conversationId) || record.conversation_id;
    if (!record.conversation_id) {
      throw new Error("completed Web execution is missing a stable conversation identity");
    }
    record.conversation_attestation = "ui_observed";
    const outputPath = resultPath(stateDir, record.execution_id);
    await mkdir(dirname(outputPath), { recursive: true });
    await writeFile(outputPath, String(terminal.response || ""), { encoding: "utf8", mode: 0o600 });
    record.result_locator = outputPath;
    record.terminal_receipt = {
      terminal_status: "completed",
      completion_observation: "ui_mutation_and_stable_reply",
      observed_at: record.terminal_at,
      response_sha256: createHash("sha256").update(String(terminal.response || "")).digest("hex"),
      response_length: Buffer.byteLength(String(terminal.response || ""))
    };
    await persistRecord(stateDir, record);
    await emitEvent(stateDir, record, "completed", {
      terminal_status: "completed",
      result_locator: outputPath,
      response_sha256: record.terminal_receipt.response_sha256
    });
    return record;
  } catch (error) {
    record.status = "failed";
    record.updated_at = nowIso();
    record.terminal_at = record.updated_at;
    record.terminal_receipt = {
      terminal_status: "failed",
      observed_at: record.terminal_at,
      error: sanitizedError(error)
    };
    await persistRecord(stateDir, record);
    await emitEvent(stateDir, record, "failed", {
      terminal_status: "failed",
      error: sanitizedError(error)
    });
    throw error;
  }
}

export async function executeWebAssignment(input, options = {}) {
  const record = await prepareExecution(input, options);
  return executePrepared(record, input, options);
}

export async function loadExecution(executionId, { stateDir = DEFAULT_STATE_DIR } = {}) {
  if (!/^wex_[0-9a-f-]{36}$/i.test(String(executionId || ""))) {
    throw new Error("invalid execution_id");
  }
  return readJson(executionPath(stateDir, executionId));
}

function parseArgs(argv) {
  const values = { command: argv[0] };
  const rest = argv.slice(1);
  for (let index = 0; index < rest.length; index += 1) {
    const arg = rest[index];
    if (!arg.startsWith("--")) throw new Error("unexpected argument: " + arg);
    const key = arg.slice(2).replaceAll("-", "_");
    const value = rest[index + 1];
    if (!value || value.startsWith("--")) throw new Error("missing value for " + arg);
    values[key] = value;
    index += 1;
  }
  return values;
}

async function cli(argv) {
  const args = parseArgs(argv);
  if (args.command === "status") {
    const record = await loadExecution(args.execution_id, {
      stateDir: args.state_dir || DEFAULT_STATE_DIR
    });
    process.stdout.write(JSON.stringify(record, null, 2) + "\n");
    return;
  }
  if (args.command !== "execute") {
    throw new Error("usage: executor.mjs execute --assignment-id ID --prompt-file FILE [--model MODEL] [--timeout-ms MS] [--state-dir DIR] | status --execution-id ID");
  }
  if (!args.assignment_id || !args.prompt_file) {
    throw new Error("--assignment-id and --prompt-file are required");
  }
  const prompt = await readFile(resolve(args.prompt_file), "utf8");
  const record = await executeWebAssignment(
    {
      assignment_id: args.assignment_id,
      task_id: args.task_id,
      attempt: Number(args.attempt || 1),
      model: args.model,
      reasoning_effort: args.reasoning_effort,
      prompt
    },
    {
      stateDir: args.state_dir || DEFAULT_STATE_DIR,
      timeoutMs: Number(args.timeout_ms || 180000),
      pollIntervalMs: Number(args.poll_interval_ms || 2000)
    }
  );
  process.stdout.write(JSON.stringify(record, null, 2) + "\n");
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  cli(process.argv.slice(2)).catch((error) => {
    process.stderr.write((error instanceof Error ? error.stack || error.message : String(error)) + "\n");
    process.exitCode = 1;
  });
}
