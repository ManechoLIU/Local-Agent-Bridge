import assert from "node:assert/strict";
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

class FakeBrowser {
  constructor({ failDispatch = false } = {}) {
    this.failDispatch = failDispatch;
    this.polls = 0;
    this.sentInput = null;
  }

  async runExclusive(operation) {
    return operation();
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
    return { created: true };
  }

  async selectMode(mode) {
    this.selectedMode = mode;
    return { selected: mode };
  }

  async sendMessage(input) {
    this.sentInput = input;
    if (this.failDispatch) throw new Error("synthetic dispatch failure");
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
