import test from "node:test";
import assert from "node:assert/strict";
import {
  SUBMIT_CONFIRM_TIMEOUT_MS,
  BROWSER_MACHINE_COMMAND_TIMEOUT_MS,
  RUNTIME_HOST_REQUEST_TIMEOUT_MS
} from "./timeout-budget.mjs";

test("Host timeout budget leaves bounded headroom around Web submit confirmation", function () {
  assert.equal(SUBMIT_CONFIRM_TIMEOUT_MS, 5000);
  assert.ok(BROWSER_MACHINE_COMMAND_TIMEOUT_MS >= SUBMIT_CONFIRM_TIMEOUT_MS + 3000);
  assert.ok(RUNTIME_HOST_REQUEST_TIMEOUT_MS >= BROWSER_MACHINE_COMMAND_TIMEOUT_MS + 2000);
  assert.ok(RUNTIME_HOST_REQUEST_TIMEOUT_MS <= 20000);
});
