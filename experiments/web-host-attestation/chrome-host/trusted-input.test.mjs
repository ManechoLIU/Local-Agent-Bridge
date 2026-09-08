import test from "node:test";
import assert from "node:assert/strict";
import { createTrustedInputController } from "./extension/trusted-input.js";
import { observationFromDebugger as realObservationFromDebugger } from "./extension/observation.js";

const CONVERSATION = "6a9e4158-cc8c-83ea-9763-05a7fd51f597";

function fixture(overrides = {}) {
  const commands = [];
  const pageOps = [];
  const target = {
    id: "target-1",
    tabId: 9,
    type: "page",
    url: "https://chatgpt.com/g/g-p-local/c/" + CONVERSATION
  };
  let resolveIndex = 0;
  const resolves = overrides.resolves || [
    {
      resolved: true,
      status: "send_control_resolved",
      send_control: {
        resolved: true,
        selector_kind: "testid_send_button",
        x: 120,
        y: 210
      }
    },
    {
      resolved: true,
      status: "send_control_resolved",
      send_control: {
        resolved: true,
        selector_kind: "testid_send_button",
        x: 120,
        y: 210
      }
    }
  ];

  const debuggerSession = {
    async sendCommand(method, params) {
      commands.push([method, params]);
      if (overrides.failCommand &&
          overrides.failCommand(method, params, commands.length)) {
        throw new Error("command failed");
      }
      if (method === "Target.getTargetInfo") {
        return {
          targetInfo: {
            targetId: overrides.freshTargetId || "target-1",
            type: "page",
            url: "https://chatgpt.com/g/g-p-local/c/" +
              (overrides.freshConversation || CONVERSATION)
          }
        };
      }
      if (method === "Page.getFrameTree") {
        return {
          frameTree: {
            frame: {
              id: "frame-1",
              loaderId: overrides.freshLoaderId || "loader-1",
              url: "https://chatgpt.com/g/g-p-local/c/" + CONVERSATION,
              securityOrigin: "https://chatgpt.com"
            }
          }
        };
      }
      return {};
    }
  };

  const controller = createTrustedInputController({
    allTargets: async () => [target],
    selectExactTarget: (targets, id) => {
      if (overrides.targetUnavailable) throw new Error("missing");
      const found = targets.find(t => t.id === id);
      if (!found) throw new Error("missing");
      return found;
    },
    observationFromDebugger: input => {
      const observed = realObservationFromDebugger(input);
      return {
        ...observed,
        browser_target_id: overrides.observedTargetId || observed.browser_target_id,
        conversation_id: overrides.observedConversation || observed.conversation_id,
        top_frame_id: overrides.freshTopFrameId || observed.top_frame_id
      };
    },
    executePageOperation: async (_tabId, operation, input) => {
      pageOps.push([operation, input]);
      if (operation === "resolve_send") {
        return resolves[Math.min(resolveIndex++, resolves.length - 1)];
      }
      if (operation === "prepare_rollback") {
        return overrides.rollbackPrepare || {
          safe_to_clear: true,
          status: "exact_owned_payload"
        };
      }
      if (operation === "verify_empty") {
        return overrides.verifyEmpty || {
          composer_empty: true,
          status: "observed"
        };
      }
      throw new Error("unexpected page operation");
    },
    withDebugger: async (_target, callback) => {
      if (overrides.debuggerAttachFails) throw new Error("attach failed");
      return await callback(
        {
          id: "target-1",
          tabId: 9,
          type: "page",
          url: "https://chatgpt.com/g/g-p-local/c/" + CONVERSATION
        },
        {
          frameTree: {
            frame: {
              id: "frame-1",
              loaderId: "loader-1",
              url: "https://chatgpt.com/g/g-p-local/c/" + CONVERSATION,
              securityOrigin: "https://chatgpt.com"
            }
          }
        },
        debuggerSession
      );
    }
  });

  return { controller, commands, pageOps };
}

function dispatchInput(extra = {}) {
  return {
    browser_target_id: "target-1",
    conversation_id: CONVERSATION,
    payload: "resume",
    initial_send_control: {
      resolved: true,
      selector_kind: "testid_send_button",
      x: 100,
      y: 200
    },
    on_before_dispatch: async () => {},
    ...extra
  };
}

test("send control coordinates are re-resolved and old prepare coordinates are never clicked", async () => {
  const f = fixture({
    resolves: [
      {
        resolved: true,
        status: "send_control_resolved",
        send_control: {
          resolved: true,
          selector_kind: "testid_send_button",
          x: 120,
          y: 210
        }
      },
      {
        resolved: true,
        status: "send_control_resolved",
        send_control: {
          resolved: true,
          selector_kind: "testid_send_button",
          x: 160,
          y: 260
        }
      },
      {
        resolved: true,
        status: "send_control_resolved",
        send_control: {
          resolved: true,
          selector_kind: "testid_send_button",
          x: 190,
          y: 290
        }
      }
    ]
  });
  const result = await f.controller.dispatchTrustedClick(dispatchInput());
  assert.equal(result.dispatch_attempted, true);
  assert.equal(result.diagnostics.coordinates_changed_since_prepare, true);
  const pressed = f.commands.find(([method, params]) =>
    method === "Input.dispatchMouseEvent" && params.type === "mousePressed"
  );
  assert.equal(pressed[1].x, 190);
  assert.equal(pressed[1].y, 290);
  assert.equal(
    f.commands.some(([method, params]) =>
      method === "Input.dispatchMouseEvent" &&
      params.type === "mousePressed" &&
      params.x === 100 &&
      params.y === 200
    ),
    false
  );
});

test("target drift after debugger attach fails closed before dispatch", async () => {
  const f = fixture({ observedConversation: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" });
  await assert.rejects(
    f.controller.dispatchTrustedClick(dispatchInput()),
    error => {
      assert.equal(error.dispatch_attempted, false);
      assert.equal(error.failure_stage, "debugger_target_validation");
      return true;
    }
  );
  assert.equal(
    f.commands.some(([method, params]) =>
      method === "Input.dispatchMouseEvent" && params.type === "mousePressed"
    ),
    false
  );
});

test("machine frame drift after WAL arm fails closed before mousePressed", async () => {
  const f = fixture({
    freshLoaderId: "loader-2"
  });
  await assert.rejects(
    f.controller.dispatchTrustedClick(dispatchInput()),
    error => {
      assert.equal(error.dispatch_attempted, false);
      assert.equal(error.failure_stage, "dispatch_arm_target_validation");
      return true;
    }
  );
  assert.equal(
    f.commands.some(([method, params]) =>
      method === "Input.dispatchMouseEvent" && params.type === "mousePressed"
    ),
    false
  );
});

test("debugger attach failure is pre-dispatch and retryable", async () => {
  const f = fixture({ debuggerAttachFails: true });
  await assert.rejects(
    f.controller.dispatchTrustedClick(dispatchInput()),
    error => {
      assert.equal(error.dispatch_attempted, false);
      assert.equal(error.status, "debugger_attach_failed");
      assert.equal(error.retryable, true);
      return true;
    }
  );
});

test("mouse move failure is pre-dispatch so caller may safely roll back exact owned payload", async () => {
  const f = fixture({
    failCommand(method, params) {
      return method === "Input.dispatchMouseEvent" && params.type === "mouseMoved";
    }
  });
  await assert.rejects(
    f.controller.dispatchTrustedClick(dispatchInput()),
    error => {
      assert.equal(error.dispatch_attempted, false);
      assert.equal(error.failure_stage, "mouse_move");
      return true;
    }
  );
});

test("mousePressed failure is result-unknown boundary and cannot be retried", async () => {
  const f = fixture({
    failCommand(method, params) {
      return method === "Input.dispatchMouseEvent" && params.type === "mousePressed";
    }
  });
  await assert.rejects(
    f.controller.dispatchTrustedClick(dispatchInput()),
    error => {
      assert.equal(error.dispatch_attempted, true);
      assert.equal(error.failure_stage, "mouse_press");
      assert.equal(error.retryable, false);
      return true;
    }
  );
});

test("send control disappearing after mouse move fails before dispatch", async () => {
  const f = fixture({
    resolves: [
      {
        resolved: true,
        status: "send_control_resolved",
        send_control: {
          resolved: true,
          selector_kind: "testid_send_button",
          x: 120,
          y: 210
        }
      },
      {
        resolved: false,
        status: "send_unavailable",
        send_control: null
      }
    ]
  });
  await assert.rejects(
    f.controller.dispatchTrustedClick(dispatchInput()),
    error => {
      assert.equal(error.dispatch_attempted, false);
      assert.equal(error.failure_stage, "send_control_revalidation");
      return true;
    }
  );
});

test("rollback clears only exact hash-authorized prepared payload with trusted key events", async () => {
  const f = fixture();
  const result = await f.controller.rollbackPreparedPayloadByHash({
    browser_target_id: "target-1",
    conversation_id: CONVERSATION,
    payload_sha256: "a".repeat(64)
  });
  assert.equal(result.cleared, true);
  assert.equal(
    f.commands.some(([method, params]) =>
      method === "Input.dispatchKeyEvent" &&
      params.type === "rawKeyDown" &&
      params.key === "Backspace"
    ),
    true
  );
});

test("rollback never presses keys when exact payload hash does not match", async () => {
  const f = fixture({
    rollbackPrepare: {
      safe_to_clear: false,
      status: "composer_hash_mismatch"
    }
  });
  const result = await f.controller.rollbackPreparedPayloadByHash({
    browser_target_id: "target-1",
    conversation_id: CONVERSATION,
    payload_sha256: "a".repeat(64)
  });
  assert.equal(result.cleared, false);
  assert.equal(result.status, "composer_hash_mismatch");
  assert.equal(
    f.commands.some(([method]) => method === "Input.dispatchKeyEvent"),
    false
  );
});
