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
    if (last?.conversationId) {
      record.conversation_id = last.conversationId;
      record.conversation_attestation = "ui_observed";
    }
    record.updated_at = nowIso();
    await persistRecord(stateDir, record);

    if (
      last?.conversationId &&
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
      const modes = await browser.listModes();
      const personalMode = selectPersonalChatMode(modes);
      const sent = await browser.sendMessage({
        prompt,
        mode: personalMode,
        model: record.model_requested || undefined,
        newChat: true,
        temporary: false,
        wait: false
      });
      return { personalMode, sent };
    }, { name: "wex-dispatch-" + record.execution_id });

    const session = await loadBrowserSessionSnapshot(modules.config);
    record.provider_observed = "chatgpt_web";
    record.mode_observed = dispatched.personalMode;
    record.status = "running";
    record.started_at = nowIso();
    record.updated_at = record.started_at;
    record.browser_session_id = session.browser_session_id;
    record.browser_session_attestation = session.browser_session_attestation;
    record.conversation_id = dispatched.sent?.conversationId || null;
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
      terminal?.model || dispatched.sent?.model || null
    );
    Object.assign(record, modelFacts);
    record.status = "completed";
    record.updated_at = nowIso();
    record.terminal_at = record.updated_at;
    record.conversation_id = terminal.conversationId || record.conversation_id;
    record.conversation_attestation = record.conversation_id ? "ui_observed" : "unavailable";
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
