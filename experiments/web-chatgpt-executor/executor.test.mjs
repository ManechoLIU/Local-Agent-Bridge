import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  classifyModelAttestation,
  executeWebAssignment,
  loadExecution,
  selectPersonalChatMode
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

  async sendMessage(input) {
    this.sentInput = input;
    if (this.failDispatch) throw new Error("synthetic dispatch failure");
    return {
      sent: true,
      waiting: false,
      conversationId: "conv-test-123"
    };
  }

  async getLatestResponse() {
    this.polls += 1;
    if (this.polls === 1) {
      return {
        conversationId: "conv-test-123",
        response: "partial",
        assistantMessageCount: 1,
        generating: true,
        model: "GPT-5.6 Sol"
      };
    }
    return {
      conversationId: "conv-test-123",
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
      timeoutMs: 2000,
      pollIntervalMs: 1
    }
  );

  assert.equal(record.status, "completed");
  assert.equal(record.assignment_id, "LAB-WEX-01-POC-01");
  assert.match(record.execution_id, /^wex_[0-9a-f-]{36}$/i);
  assert.equal(record.conversation_id, "conv-test-123");
  assert.match(record.browser_session_id, /^bwex_[0-9a-f]{16}$/);
  assert.equal(record.mode_observed, "聊天");
  assert.equal(record.model_attestation, "partial");
  assert.equal(record.model_match, true);
  assert.equal(await readFile(record.result_locator, "utf8"), "done");
  assert.equal(browser.sentInput.mode, "聊天");
  assert.equal(browser.sentInput.newChat, true);
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
  assert.deepEqual(events.map((event) => event.event), ["queued", "started", "completed"]);
  assert.equal(events.at(-1).conversation_id, "conv-test-123");
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
      { stateDir, browserModules: modules, timeoutMs: 500, pollIntervalMs: 1 }
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
