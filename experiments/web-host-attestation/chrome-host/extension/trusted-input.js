function trustedDispatchError(message, options) {
  const error = new Error(message);
  error.dispatch_attempted = Boolean(options && options.dispatch_attempted);
  error.retryable = Boolean(options && options.retryable);
  error.status = options && options.status || "trusted_dispatch_failed";
  error.failure_stage = options && options.failure_stage || "trusted_dispatch";
  error.diagnostics = options && options.diagnostics || null;
  return error;
}

function pointChanged(initial, current) {
  if (!initial || initial.resolved !== true || !current || current.resolved !== true) return false;
  return Math.abs(Number(initial.x) - Number(current.x)) > 0.5 ||
    Math.abs(Number(initial.y) - Number(current.y)) > 0.5;
}

export function createTrustedInputController(options) {
  const allTargets = options && options.allTargets;
  const withDebugger = options && options.withDebugger;
  const executePageOperation = options && options.executePageOperation;
  const observationFromDebugger = options && options.observationFromDebugger;
  const selectExactTarget = options && options.selectExactTarget;

  if (typeof allTargets !== "function" || typeof withDebugger !== "function" ||
      typeof executePageOperation !== "function" || typeof observationFromDebugger !== "function" ||
      typeof selectExactTarget !== "function") {
    throw new Error("trusted input controller dependencies are required");
  }

  async function dispatchTrustedClick(input) {
    const browserTargetId = String(input && input.browser_target_id || "").trim();
    const conversationId = String(input && input.conversation_id || "").trim();
    const payload = String(input && input.payload || "");
    const initialSendControl = input && input.initial_send_control || null;
    const onBeforeDispatch = input && input.on_before_dispatch;
    const diagnostics = {
      debugger_attach_attempted: true,
      debugger_attached: false,
      send_control_resolution_attempted: false,
      send_control_resolved: false,
      selector_kind: null,
      coordinates_changed_since_prepare: false,
      focus_emulation: false,
      mouse_moved: false,
      mouse_pressed: false,
      mouse_released: false
    };

    let target;
    try {
      target = selectExactTarget(await allTargets(), browserTargetId);
    } catch {
      throw trustedDispatchError("trusted click target is unavailable", {
        dispatch_attempted: false,
        retryable: false,
        status: "target_unavailable_before_trusted_dispatch",
        failure_stage: "target_before_dispatch",
        diagnostics
      });
    }
    if (!Number.isInteger(target.tabId)) {
      throw trustedDispatchError("trusted click target has no tabId", {
        dispatch_attempted: false,
        retryable: false,
        status: "target_tab_unavailable_before_trusted_dispatch",
        failure_stage: "target_before_dispatch",
        diagnostics
      });
    }

    try {
      return await withDebugger(target, async function (targetInfo, frameTree, debuggerSession) {
        diagnostics.debugger_attached = true;
        const observation = observationFromDebugger({
          targetInfo,
          frameTree,
          observedAtMs: Date.now()
        });
        if (observation.browser_target_id !== browserTargetId ||
            observation.conversation_id !== conversationId) {
          throw trustedDispatchError("trusted click target changed after debugger attach", {
            dispatch_attempted: false,
            retryable: false,
            status: "target_changed_after_debugger_attach",
            failure_stage: "debugger_target_validation",
            diagnostics
          });
        }

        try {
          await debuggerSession.sendCommand("Emulation.setFocusEmulationEnabled", { enabled: true });
          diagnostics.focus_emulation = true;
        } catch {
          throw trustedDispatchError("background focus emulation failed", {
            dispatch_attempted: false,
            retryable: true,
            status: "focus_emulation_unavailable",
            failure_stage: "focus_emulation",
            diagnostics
          });
        }

        try {
          diagnostics.send_control_resolution_attempted = true;
          const first = await executePageOperation(target.tabId, "resolve_send", {
            payload,
            expected_conversation_id: conversationId
          });
          if (!first || first.resolved !== true || !first.send_control) {
            throw trustedDispatchError("send control is unavailable before trusted dispatch", {
              dispatch_attempted: false,
              retryable: first && first.status === "controller_active",
              status: first && first.status || "send_unavailable",
              failure_stage: "send_control_resolution",
              diagnostics
            });
          }

          const firstPoint = {
            x: Number(first.send_control.x),
            y: Number(first.send_control.y)
          };
          diagnostics.selector_kind = first.send_control.selector_kind || null;
          try {
            await debuggerSession.sendCommand("Input.dispatchMouseEvent", {
              type: "mouseMoved",
              x: firstPoint.x,
              y: firstPoint.y,
              button: "none"
            });
            diagnostics.mouse_moved = true;
          } catch {
            throw trustedDispatchError("trusted mouse move failed before dispatch", {
              dispatch_attempted: false,
              retryable: true,
              status: "mouse_move_failed_before_dispatch",
              failure_stage: "mouse_move",
              diagnostics
            });
          }

          const second = await executePageOperation(target.tabId, "resolve_send", {
            payload,
            expected_conversation_id: conversationId
          });
          if (!second || second.resolved !== true || !second.send_control) {
            throw trustedDispatchError("send control changed before mouse press", {
              dispatch_attempted: false,
              retryable: false,
              status: second && second.status || "send_control_changed_before_dispatch",
              failure_stage: "send_control_revalidation",
              diagnostics
            });
          }

          diagnostics.send_control_resolved = true;
          diagnostics.coordinates_changed_since_prepare = pointChanged(
            initialSendControl,
            second.send_control
          );
          diagnostics.selector_kind = second.send_control.selector_kind || diagnostics.selector_kind;
          const finalPoint = {
            x: Number(second.send_control.x),
            y: Number(second.send_control.y)
          };
          if (Math.abs(finalPoint.x - firstPoint.x) > 0.5 ||
              Math.abs(finalPoint.y - firstPoint.y) > 0.5) {
            try {
              await debuggerSession.sendCommand("Input.dispatchMouseEvent", {
                type: "mouseMoved",
                x: finalPoint.x,
                y: finalPoint.y,
                button: "none"
              });
            } catch {
              throw trustedDispatchError("trusted mouse re-position failed before dispatch", {
                dispatch_attempted: false,
                retryable: true,
                status: "mouse_move_failed_before_dispatch",
                failure_stage: "mouse_move",
                diagnostics
              });
            }
          }

          if (typeof onBeforeDispatch === "function") {
            try {
              await onBeforeDispatch({
                debugger_attached: diagnostics.debugger_attached,
                send_control_resolved: diagnostics.send_control_resolved,
                selector_kind: diagnostics.selector_kind,
                coordinates_changed_since_prepare: diagnostics.coordinates_changed_since_prepare,
                focus_emulation: diagnostics.focus_emulation
              });
            } catch {
              throw trustedDispatchError("dispatch journal boundary could not be persisted", {
                dispatch_attempted: false,
                retryable: false,
                status: "dispatch_boundary_persist_failed",
                failure_stage: "dispatch_boundary",
                diagnostics
              });
            }
          }

          let dispatchStarted = false;
          try {
            dispatchStarted = true;
            await debuggerSession.sendCommand("Input.dispatchMouseEvent", {
              type: "mousePressed",
              x: finalPoint.x,
              y: finalPoint.y,
              button: "left",
              buttons: 1,
              clickCount: 1
            });
            diagnostics.mouse_pressed = true;
            await debuggerSession.sendCommand("Input.dispatchMouseEvent", {
              type: "mouseReleased",
              x: finalPoint.x,
              y: finalPoint.y,
              button: "left",
              buttons: 0,
              clickCount: 1
            });
            diagnostics.mouse_released = true;
          } catch {
            throw trustedDispatchError("trusted mouse dispatch result is uncertain", {
              dispatch_attempted: dispatchStarted,
              retryable: false,
              status: "trusted_dispatch_result_unknown",
              failure_stage: diagnostics.mouse_pressed
                ? "mouse_release"
                : "mouse_press",
              diagnostics
            });
          }

          return {
            dispatched: true,
            dispatch_attempted: true,
            browser_target_id: browserTargetId,
            diagnostics: { ...diagnostics }
          };
        } finally {
          if (diagnostics.focus_emulation) {
            try {
              await debuggerSession.sendCommand("Emulation.setFocusEmulationEnabled", { enabled: false });
            } catch {
            }
          }
        }
      });
    } catch (error) {
      if (typeof error?.dispatch_attempted === "boolean") throw error;
      throw trustedDispatchError("debugger attach or session failed before trusted dispatch", {
        dispatch_attempted: false,
        retryable: true,
        status: "debugger_attach_failed",
        failure_stage: "debugger_attach",
        diagnostics
      });
    }
  }

  async function rollbackPreparedPayloadByHash(record) {
    const browserTargetId = String(record && record.browser_target_id || "").trim();
    const conversationId = String(record && record.conversation_id || "").trim();
    const payloadSha256 = String(record && record.payload_sha256 || "").trim().toLowerCase();
    const target = selectExactTarget(await allTargets(), browserTargetId);
    if (!Number.isInteger(target.tabId)) {
      return { cleared: false, already_absent: false, status: "target_tab_unavailable" };
    }

    return await withDebugger(target, async function (targetInfo, frameTree, debuggerSession) {
      const observation = observationFromDebugger({
        targetInfo,
        frameTree,
        observedAtMs: Date.now()
      });
      if (observation.browser_target_id !== browserTargetId ||
          observation.conversation_id !== conversationId) {
        return { cleared: false, already_absent: false, status: "target_changed" };
      }

      await debuggerSession.sendCommand("Emulation.setFocusEmulationEnabled", { enabled: true });
      try {
        const safe = await executePageOperation(target.tabId, "prepare_rollback", {
          expected_payload_sha256: payloadSha256,
          expected_conversation_id: conversationId
        });
        if (safe && safe.status === "already_absent" && safe.composer_empty === true) {
          return { cleared: false, already_absent: true, status: "already_absent" };
        }
        if (!safe || safe.safe_to_clear !== true) {
          return {
            cleared: false,
            already_absent: false,
            status: safe && safe.status || "rollback_not_authorized"
          };
        }

        await debuggerSession.sendCommand("Input.dispatchKeyEvent", {
          type: "rawKeyDown",
          key: "a",
          code: "KeyA",
          modifiers: 4,
          windowsVirtualKeyCode: 65
        });
        await debuggerSession.sendCommand("Input.dispatchKeyEvent", {
          type: "keyUp",
          key: "a",
          code: "KeyA",
          modifiers: 4,
          windowsVirtualKeyCode: 65
        });
        await debuggerSession.sendCommand("Input.dispatchKeyEvent", {
          type: "rawKeyDown",
          key: "Backspace",
          code: "Backspace",
          windowsVirtualKeyCode: 8
        });
        await debuggerSession.sendCommand("Input.dispatchKeyEvent", {
          type: "keyUp",
          key: "Backspace",
          code: "Backspace",
          windowsVirtualKeyCode: 8
        });

        const verification = await executePageOperation(target.tabId, "verify_empty", {
          expected_conversation_id: conversationId
        });
        return {
          cleared: Boolean(verification && verification.composer_empty),
          already_absent: false,
          status: verification && verification.composer_empty
            ? "rolled_back"
            : "rollback_unconfirmed"
        };
      } finally {
        try {
          await debuggerSession.sendCommand("Emulation.setFocusEmulationEnabled", { enabled: false });
        } catch {
        }
      }
    });
  }

  return Object.freeze({
    dispatchTrustedClick,
    rollbackPreparedPayloadByHash
  });
}
