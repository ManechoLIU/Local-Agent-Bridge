import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  chooseRequestedModel,
  classifyModelAttestation,
  executeWebAssignment,
  loadExecution,
  reasoningEffortIndex,
  selectPersonalChatMode,
  stableConversationId
} from "./executor.mjs";
import { EXPOSURE_STATE, LOCAL_AGENT_BRIDGE_APP } from "./tool-exposure.mjs";

class FakeBrowser {
  constructor({
    failDispatch = false,
    failAppConfirm = false,
    appsAfterReset = []
  } = {}) {
    this.failDispatch = failDispatch;
    this.failAppConfirm = failAppConfirm;
    this.appsAfterReset = [...appsAfterReset];
    this.composerApps = new Set(this.appsAfterReset);
    this.polls = 0;
    this.sentInput = null;
    this.sendCalls = [];
    this.selectAppCalls = [];
    this.ensureAppExposureCalls = [];
    this.observeAppExposureCalls = [];
    this.writePromptCalls = [];
    this.submitPromptCalls = [];
    this.callOrder = [];
    this.composerText = "";
  }

  resetTurn() {
    this.composerApps = new Set(this.appsAfterReset);
  }

  async runExclusive(operation) {
    return operation();
  }

  async status() {
    return {
      conversationId:
        this.currentConversationId || "6a9ea545-a168-83e9-aedd-25330fad4b66"
    };
  }

  async listModes() {
    return {
      current: "工作",
      modes: [
        { name: "工作", selected: true },
        { name: "聊天", selected: false }
      ]
    };
  }

  async newChat() {
    this.newChatCalled = true;
    this.resetTurn();
    return { created: true };
  }

  async observeAppExposure(appName) {
    this.observeAppExposureCalls.push(appName);
    const exposed = this.composerApps.has(appName);
    return {
      app: appName,
      exposed,
      confirmed: exposed,
      state: exposed ? EXPOSURE_STATE.CONFIRMED : EXPOSURE_STATE.ABSENT_REPAIRABLE,
      observed_apps: [...this.composerApps]
    };
  }

  async selectApp(appName) {
    this.selectAppCalls.push(appName);
    this.callOrder.push("selectApp:" + appName);
    if (this.failAppConfirm) {
      return {
        app: appName,
        exposed: false,
        confirmed: false,
        state: EXPOSURE_STATE.UNAVAILABLE,
        observed_apps: [...this.composerApps]
      };
    }
    this.composerApps.add(appName);
    return {
      app: appName,
      exposed: true,
      confirmed: true,
      state: EXPOSURE_STATE.CONFIRMED,
      observed_apps: [...this.composerApps],
      ui_mention_used: true
    };
  }

  async ensureAppExposure(appName) {
    this.ensureAppExposureCalls.push(appName);
    const observed = await this.observeAppExposure(appName);
    if (observed.confirmed) {
      return { ...observed, action: "already_exposed", attempts: 0 };
    }
    const selected = await this.selectApp(appName);
    return {
      ...selected,
      action: this.failAppConfirm ? "failed" : "selected",
      attempts: 1
    };
  }

  async selectMode(mode) {
    this.selectedMode = mode;
    return { selected: mode };
  }

  async writePrompt(prompt) {
    this.writePromptCalls.push(prompt);
    this.callOrder.push("writePrompt");
    this.composerText = prompt;
    // Real chatgpt-web-mcp writePrompt() uses locator.fill(), which replaces
    // composer contents and therefore removes any previously inserted app chip.
    this.composerApps.clear();
    return { written: true, characters: prompt.length };
  }

  async submitPrompt(input = {}) {
    this.submitPromptCalls.push(input);
    this.callOrder.push("submitPrompt");
    if (this.failDispatch) throw new Error("synthetic dispatch failure");
    this.submittedPrompt = this.composerText;
    this.submittedApps = [...this.composerApps];
    this.composerApps.clear();
    return {
      sent: true,
      waiting: false,
      conversationId: "WEB"
    };
  }

  async sendMessage(input) {
    this.sentInput = input;
    this.sendCalls.push(input);
    this.callOrder.push("sendMessage");
    if (this.failDispatch) throw new Error("synthetic dispatch failure");
    this.composerApps.clear();
    return {
      sent: true,
      waiting: false,
      conversationId: "WEB"
    };
  }

  async getLatestResponse() {
    this.polls += 1;
    if (this.polls === 1) {
      return {
        conversationId: "6a9ea545-a168-83e9-aedd-25330fad4b66",
        response: "partial",
        assistantMessageCount: 1,
        generating: true,
        model: "GPT-5.6 Sol"
      };
    }
    return {
      conversationId: "6a9ea545-a168-83e9-aedd-25330fad4b66",
      response: "done",
      assistantMessageCount: 1,
      generating: false,
      model: "GPT-5.6 Sol"
    };
  }
}

async function fakeModules(browser, dir) {
  const browserState = join(dir, "browser-state.json");
  await writeFile(
    browserState,
    JSON.stringify({ startedAt: 123456789, pid: 4321, port: 9222 })
  );
  return {
    createBrowser: () => browser,
    config: { BROWSER_STATE_FILE: browserState }
  };
}

function executionInput(overrides = {}) {
  return {
    assignment_id: "LAB-WEX-01-POC-01",
    task_id: "LAB-WEX-01",
    model: "GPT-5.6 Sol",
    prompt: "Return exactly WEB_EXECUTOR_POC_OK.",
    ...overrides
  };
}

function executionOptions(browser, modules, overrides = {}) {
  return {
    browserModules: modules,
    modelRouter: async (_browser, requested) => ({
      available: ["GPT-5.6 Sol", "GPT-5.5"],
      selected: requested || "GPT-5.6 Sol",
      changed: false
    }),
    reasoningRouter: async (_browser, requested) => ({
      requested,
      observed_index: 1,
      observed_label: "Medium",
      attestation: "partial",
      changed: true
    }),
    timeoutMs: 2000,
    pollIntervalMs: 1,
    ...overrides
  };
}

test("selectPersonalChatMode rejects Work and selects ordinary Chat", () => {
  assert.equal(
    selectPersonalChatMode({
      current: "Work",
      modes: [
        { name: "Work", selected: true },
        { name: "Chat", selected: false }
      ]
    }),
    "Chat"
  );
  assert.throws(
    () => selectPersonalChatMode({ modes: [{ name: "Work", selected: true }] }),
    /cannot be uniquely verified/
  );
});

test("stableConversationId rejects placeholders and accepts stable IDs", () => {
  assert.equal(stableConversationId("WEB"), null);
  assert.equal(stableConversationId("pending"), null);
  assert.equal(
    stableConversationId("6a9ea545-a168-83e9-aedd-25330fad4b66"),
    "6a9ea545-a168-83e9-aedd-25330fad4b66"
  );
});

test("chooseRequestedModel fails closed on unavailable or ambiguous models", () => {
  assert.equal(
    chooseRequestedModel("GPT-5.6 Sol", ["GPT-5.6 Sol", "GPT-5.5"]),
    "GPT-5.6 Sol"
  );
  assert.throws(
    () => chooseRequestedModel("GPT", ["GPT-5.6 Sol", "GPT-5.5"]),
    /not uniquely available/
  );
  assert.throws(
    () => chooseRequestedModel("GPT-9", ["GPT-5.6 Sol", "GPT-5.5"]),
    /not uniquely available/
  );
});

test("reasoningEffortIndex maps Runtime effort to the three Web UI tiers", () => {
  assert.equal(reasoningEffortIndex("low"), 0);
  assert.equal(reasoningEffortIndex("medium"), 1);
  assert.equal(reasoningEffortIndex("high"), 2);
  assert.equal(reasoningEffortIndex("xhigh"), 2);
  assert.equal(reasoningEffortIndex("max"), 2);
  assert.throws(() => reasoningEffortIndex("impossible"), /unsupported/);
});

test("model attestation stays partial because the model is only UI-observed", () => {
  assert.deepEqual(classifyModelAttestation("GPT-5.6 Sol", "GPT-5.6 Sol"), {
    model_requested: "GPT-5.6 Sol",
    model_observed: "GPT-5.6 Sol",
    model_attestation: "partial",
    model_match: true
  });
  assert.equal(classifyModelAttestation("GPT-5.6 Sol", null).model_attestation, "unavailable");
});

test("execution creates durable assignment/execution/conversation correlation and terminal result", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "lab-wex-"));
  const browser = new FakeBrowser();
  const modules = await fakeModules(browser, stateDir);
  const prompt = "Return exactly WEB_EXECUTOR_POC_OK.";

  const record = await executeWebAssignment(
    {
      assignment_id: "LAB-WEX-01-POC-01",
      task_id: "LAB-WEX-01",
      model: "GPT-5.6 Sol",
      prompt
    },
    {
      stateDir,
      browserModules: modules,
      modelRouter: async (_browser, requested) => ({
        available: ["GPT-5.6 Sol", "GPT-5.5"],
        selected: requested || "GPT-5.6 Sol",
        changed: false
      }),
      reasoningRouter: async (_browser, requested) => ({
        requested,
        observed_index: 1,
        observed_label: "Medium",
        attestation: "partial",
        changed: true
      }),
      timeoutMs: 2000,
      pollIntervalMs: 1
    }
  );

  assert.equal(record.status, "completed");
  assert.equal(record.assignment_id, "LAB-WEX-01-POC-01");
  assert.match(record.execution_id, /^wex_[0-9a-f-]{36}$/i);
  assert.equal(record.conversation_id, "6a9ea545-a168-83e9-aedd-25330fad4b66");
  assert.match(record.browser_session_id, /^bwex_[0-9a-f]{16}$/);
  assert.equal(record.mode_observed, "聊天");
  assert.equal(record.model_attestation, "partial");
  assert.equal(record.model_match, true);
  assert.equal(await readFile(record.result_locator, "utf8"), "done");
  assert.equal(record.mode_observed, "聊天");
  assert.equal(browser.newChatCalled, true);
  assert.equal(browser.sentInput.newChat, false);
  assert.equal(browser.sentInput.temporary, false);
  assert.equal(browser.sentInput.wait, false);

  const persisted = await loadExecution(record.execution_id, { stateDir });
  assert.equal(persisted.status, "completed");
  const raw = await readFile(join(stateDir, "executions", record.execution_id + ".json"), "utf8");
  assert.equal(raw.includes(prompt), false);
  assert.equal(raw.includes(record.prompt_sha256), true);

  const events = (await readFile(join(stateDir, "events.jsonl"), "utf8"))
    .trim()
    .split("\n")
    .map(JSON.parse);
  assert.deepEqual(events.map((event) => event.event), [
    "queued",
    "started",
    "conversation_bound",
    "completed"
  ]);
  assert.equal(events[1].conversation_id, null);
  assert.equal(events[2].conversation_id, "6a9ea545-a168-83e9-aedd-25330fad4b66");
  assert.equal(events.at(-1).conversation_id, "6a9ea545-a168-83e9-aedd-25330fad4b66");
});

test("failed dispatch emits failed terminal without fabricating a conversation", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "lab-wex-fail-"));
  const browser = new FakeBrowser({ failDispatch: true });
  const modules = await fakeModules(browser, stateDir);

  await assert.rejects(
    executeWebAssignment(
      {
        assignment_id: "LAB-WEX-01-POC-FAIL",
        task_id: "LAB-WEX-01",
        prompt: "test"
      },
      {
        stateDir,
        browserModules: modules,
        modelRouter: async () => ({
          available: ["GPT-5.6 Sol", "GPT-5.5"],
          selected: "GPT-5.6 Sol",
          changed: false
        }),
        reasoningRouter: async () => ({
          requested: null,
          observed_index: null,
          observed_label: null,
          attestation: "unavailable",
          changed: false
        }),
        timeoutMs: 500,
        pollIntervalMs: 1
      }
    ),
    /synthetic dispatch failure/
  );

  const events = (await readFile(join(stateDir, "events.jsonl"), "utf8"))
    .trim()
    .split("\n")
    .map(JSON.parse);
  assert.deepEqual(events.map((event) => event.event), ["queued", "failed"]);
  assert.equal(events.at(-1).conversation_id, null);
});

test("required app turn writes prompt before app exposure and submits without sendMessage", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "lab-wex-exp-ready-"));
  const browser = new FakeBrowser({ appsAfterReset: [LOCAL_AGENT_BRIDGE_APP] });
  const modules = await fakeModules(browser, stateDir);
  const prompt = "Return exactly WEB_EXECUTOR_POC_OK.";

  const record = await executeWebAssignment(
    executionInput({
      local_tool_app_required: true,
      prompt
    }),
    executionOptions(browser, modules, { stateDir })
  );

  assert.equal(record.status, "completed");
  assert.equal(record.tool_exposure.state, EXPOSURE_STATE.CONFIRMED);
  assert.equal(record.tool_exposure.action, "selected");
  assert.equal(record.tool_exposure.attempts, 1);
  assert.deepEqual(record.tool_exposure.required_apps, [LOCAL_AGENT_BRIDGE_APP]);
  assert.equal(browser.sendCalls.length, 0);
  assert.deepEqual(browser.writePromptCalls, [prompt]);
  assert.equal(browser.submitPromptCalls.length, 1);
  assert.deepEqual(browser.selectAppCalls, [LOCAL_AGENT_BRIDGE_APP]);
  assert.deepEqual(browser.submittedApps, [LOCAL_AGENT_BRIDGE_APP]);
  assert.equal(browser.submittedPrompt, prompt);
  assert.deepEqual(browser.callOrder.filter((item) => item === "writePrompt" || item.startsWith("selectApp:") || item === "submitPrompt"), [
    "writePrompt",
    "selectApp:" + LOCAL_AGENT_BRIDGE_APP,
    "submitPrompt"
  ]);
});

test("reuse without an explicit stable conversation binding fails closed", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "lab-wex-reuse-unbound-"));
  const browser = new FakeBrowser();
  const modules = await fakeModules(browser, stateDir);

  await assert.rejects(
    executeWebAssignment(
      executionInput({ new_chat: false }),
      executionOptions(browser, modules, { stateDir })
    ),
    /conversation.*required|bound conversation|reuse/i
  );
  assert.equal(browser.sendCalls.length, 0);
  assert.equal(browser.submitPromptCalls.length, 0);
});

test("reuse rejects a different active ChatGPT conversation", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "lab-wex-reuse-mismatch-"));
  const browser = new FakeBrowser();
  browser.currentConversationId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const modules = await fakeModules(browser, stateDir);

  await assert.rejects(
    executeWebAssignment(
      executionInput({
        new_chat: false,
        conversation_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
      }),
      executionOptions(browser, modules, { stateDir })
    ),
    /conversation.*match|conversation.*mismatch|bound conversation/i
  );
  assert.equal(browser.sendCalls.length, 0);
  assert.equal(browser.submitPromptCalls.length, 0);
});

test("next turn hydration re-exposes Local Agent Bridge before send", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "lab-wex-exp-hydrate-"));
  const browser = new FakeBrowser({ appsAfterReset: [LOCAL_AGENT_BRIDGE_APP] });
  const modules = await fakeModules(browser, stateDir);

  const first = await executeWebAssignment(
    executionInput({
      assignment_id: "LAB-WEX-EXP-TURN-1",
      local_tool_app_required: true
    }),
    executionOptions(browser, modules, { stateDir })
  );
  assert.equal(browser.sendCalls.length, 0);
  assert.equal(browser.submitPromptCalls.length, 1);
  assert.deepEqual(browser.selectAppCalls, [LOCAL_AGENT_BRIDGE_APP]);
  assert.equal(browser.composerApps.size, 0);

  browser.appsAfterReset = [];
  const second = await executeWebAssignment(
    executionInput({
      assignment_id: "LAB-WEX-EXP-TURN-2",
      local_tool_app_required: true,
      new_chat: false,
      conversation_id: first.conversation_id
    }),
    executionOptions(browser, modules, { stateDir })
  );

  assert.equal(second.status, "completed");
  assert.equal(second.tool_exposure.state, EXPOSURE_STATE.CONFIRMED);
  assert.equal(second.tool_exposure.action, "selected");
  assert.deepEqual(browser.selectAppCalls, [LOCAL_AGENT_BRIDGE_APP, LOCAL_AGENT_BRIDGE_APP]);
  assert.equal(browser.sendCalls.length, 0);
  assert.equal(browser.submitPromptCalls.length, 2);
  assert.equal(second.conversation_attestation, "ui_observed");
  assert.notEqual(second.tool_exposure.state, second.browser_session_attestation);
});

test("unconfirmed app exposure fails closed before prompt dispatch", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "lab-wex-exp-fail-"));
  const browser = new FakeBrowser({ failAppConfirm: true, appsAfterReset: [] });
  const modules = await fakeModules(browser, stateDir);

  await assert.rejects(
    executeWebAssignment(
      executionInput({
        assignment_id: "LAB-WEX-EXP-FAIL",
        local_tool_app_required: true
      }),
      executionOptions(browser, modules, { stateDir })
    ),
    /not confirmed|UNAVAILABLE|ABSENT_REPAIRABLE/
  );

  assert.equal(browser.sendCalls.length, 0);
  assert.equal(browser.submitPromptCalls.length, 0);
  assert.equal(browser.sentInput, null);
  assert.deepEqual(browser.selectAppCalls, [LOCAL_AGENT_BRIDGE_APP]);
  assert.equal(browser.selectAppCalls.includes("AI-Bridge"), false);
  assert.equal(browser.selectAppCalls.includes("Local Bridge Identity Probe"), false);

  const events = (await readFile(join(stateDir, "events.jsonl"), "utf8"))
    .trim()
    .split("\n")
    .map(JSON.parse);
  assert.equal(events.at(-1).event, "failed");
  const persisted = JSON.parse(
    await readFile(join(stateDir, "executions", events.at(-1).execution_id + ".json"), "utf8")
  );
  assert.equal(persisted.tool_exposure.confirmed, false);
  assert.match(
    String(persisted.tool_exposure.state),
    /UNAVAILABLE|ABSENT_REPAIRABLE/
  );
});


test("required app turn fails closed when browser lacks safe writePrompt plus submitPrompt boundary", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "lab-wex-exp-boundary-"));
  const browser = new FakeBrowser({ appsAfterReset: [] });
  browser.writePrompt = undefined;
  browser.submitPrompt = undefined;
  const modules = await fakeModules(browser, stateDir);

  await assert.rejects(
    executeWebAssignment(
      executionInput({
        assignment_id: "LAB-WEX-EXP-BOUNDARY",
        local_tool_app_required: true
      }),
      executionOptions(browser, modules, { stateDir })
    ),
    /safe.*writePrompt.*submitPrompt|writePrompt.*submitPrompt|required Web tool app/i
  );

  assert.equal(browser.sendCalls.length, 0);
  assert.deepEqual(browser.selectAppCalls, []);
});

test("executions that do not require LAB never call app selection", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "lab-wex-exp-none-"));
  const browser = new FakeBrowser();
  const modules = await fakeModules(browser, stateDir);

  const record = await executeWebAssignment(
    executionInput({ assignment_id: "LAB-WEX-REASONING" }),
    executionOptions(browser, modules, { stateDir })
  );

  assert.equal(record.status, "completed");
  assert.deepEqual(record.tool_exposure?.required_apps || [], []);
  assert.deepEqual(browser.selectAppCalls, []);
  assert.deepEqual(browser.ensureAppExposureCalls, []);
  assert.deepEqual(browser.observeAppExposureCalls, []);
  assert.equal(browser.sendCalls.length, 1);
  assert.equal(browser.writePromptCalls.length, 0);
  assert.equal(browser.submitPromptCalls.length, 0);
});

test("CRLF logical prompt uses the same canonical hash as DOM LF text", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "lab-wex-exp-crlf-"));
  const browser = new FakeBrowser({ appsAfterReset: [] });
  const modules = await fakeModules(browser, stateDir);
  const prompt = "line one\r\nline two";

  browser.observeAppExposure = async function(appName, options = {}) {
    this.observeAppExposureCalls.push(appName);
    const domText = this.composerText.replace(/\r\n?/g, "\n");
    const observedHash = createHash("sha256").update(domText).digest("hex");
    if (String(options.expected_prompt_sha256 || "") !== observedHash) {
      return {
        app: appName,
        state: EXPOSURE_STATE.UNAVAILABLE,
        available: false,
        observed_apps: []
      };
    }
    const exposed = this.composerApps.has(appName);
    return {
      app: appName,
      state: exposed ? EXPOSURE_STATE.CONFIRMED : EXPOSURE_STATE.ABSENT_REPAIRABLE,
      observed_apps: [...this.composerApps]
    };
  };
  browser.selectApp = async function(appName, options = {}) {
    this.selectAppCalls.push(appName);
    const domText = this.composerText.replace(/\r\n?/g, "\n");
    const observedHash = createHash("sha256").update(domText).digest("hex");
    if (String(options.expected_prompt_sha256 || "") !== observedHash) {
      return {
        app: appName,
        state: EXPOSURE_STATE.UNAVAILABLE,
        available: false,
        observed_apps: []
      };
    }
    this.composerApps.add(appName);
    return {
      app: appName,
      state: EXPOSURE_STATE.CONFIRMED,
      observed_apps: [...this.composerApps],
      ui_mention_used: true
    };
  };

  const record = await executeWebAssignment(
    executionInput({
      assignment_id: "LAB-WEX-EXP-CRLF",
      local_tool_app_required: true,
      prompt
    }),
    executionOptions(browser, modules, { stateDir })
  );

  assert.equal(record.status, "completed");
  assert.equal(record.tool_exposure.state, EXPOSURE_STATE.CONFIRMED);
  assert.equal(
    record.prompt_sha256,
    createHash("sha256").update("line one\nline two").digest("hex")
  );
});

test("failed repairable exposure persists ABSENT_REPAIRABLE instead of UNKNOWN", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "lab-wex-exp-repairable-"));
  const browser = new FakeBrowser({ appsAfterReset: [] });
  const modules = await fakeModules(browser, stateDir);

  browser.observeAppExposure = async function(appName) {
    this.observeAppExposureCalls.push(appName);
    return {
      app: appName,
      state: EXPOSURE_STATE.ABSENT_REPAIRABLE,
      observed_apps: []
    };
  };
  browser.selectApp = async function(appName) {
    this.selectAppCalls.push(appName);
    return {
      app: appName,
      state: EXPOSURE_STATE.ABSENT_REPAIRABLE,
      observed_apps: []
    };
  };

  await assert.rejects(
    executeWebAssignment(
      executionInput({
        assignment_id: "LAB-WEX-EXP-REPAIRABLE",
        local_tool_app_required: true
      }),
      executionOptions(browser, modules, { stateDir })
    ),
    /ABSENT_REPAIRABLE|not confirmed/i
  );

  const events = (await readFile(join(stateDir, "events.jsonl"), "utf8"))
    .trim()
    .split("\n")
    .map(JSON.parse);
  const persisted = JSON.parse(
    await readFile(join(stateDir, "executions", events.at(-1).execution_id + ".json"), "utf8")
  );
  assert.equal(persisted.tool_exposure.state, EXPOSURE_STATE.ABSENT_REPAIRABLE);
  assert.equal(persisted.tool_exposure.confirmed, false);
});

test("prompt hash and logs keep the logical prompt when UI mention is used", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "lab-wex-exp-hash-"));
  const browser = new FakeBrowser({ appsAfterReset: [] });
  const modules = await fakeModules(browser, stateDir);
  const prompt = "Return exactly WEB_EXECUTOR_POC_OK.";
  const mention = "@Local Agent Bridge";

  const record = await executeWebAssignment(
    executionInput({
      assignment_id: "LAB-WEX-EXP-HASH",
      local_tool_app_required: true,
      prompt
    }),
    executionOptions(browser, modules, { stateDir })
  );

  assert.equal(record.prompt_sha256, createHash("sha256").update(prompt).digest("hex"));
  assert.deepEqual(browser.writePromptCalls, [prompt]);
  assert.equal(browser.submittedPrompt, prompt);
  assert.equal(browser.sendCalls.length, 0);
  assert.equal(browser.submittedPrompt.includes(mention), false);
  assert.equal(record.tool_exposure.ui_mention_used, true);
  assert.equal(record.tool_exposure.required_apps.includes(LOCAL_AGENT_BRIDGE_APP), true);

  const raw = await readFile(join(stateDir, "executions", record.execution_id + ".json"), "utf8");
  const events = await readFile(join(stateDir, "events.jsonl"), "utf8");
  assert.equal(raw.includes(mention), false);
  assert.equal(events.includes(mention), false);
  assert.equal(raw.includes(prompt), false);
});
