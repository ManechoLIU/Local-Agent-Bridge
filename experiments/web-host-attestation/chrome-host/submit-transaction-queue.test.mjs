import test from "node:test";
import assert from "node:assert/strict";
import { createSubmitTransactionQueue } from "./extension/submit-transaction-queue.js";

test("startup recovery and submits are serialized without overlap", async () => {
  const queue = createSubmitTransactionQueue();
  const events = [];
  let releaseRecovery;
  const recoveryGate = new Promise(resolve => { releaseRecovery = resolve; });

  const recovery = queue.run(async () => {
    events.push("recovery:start");
    await recoveryGate;
    events.push("recovery:end");
  });

  const submitA = queue.run(async () => {
    events.push("submitA:start");
    await Promise.resolve();
    events.push("submitA:end");
  });

  const submitB = queue.run(async () => {
    events.push("submitB:start");
    events.push("submitB:end");
  });

  await Promise.resolve();
  assert.deepEqual(events, ["recovery:start"]);
  releaseRecovery();
  await Promise.all([recovery, submitA, submitB]);
  assert.deepEqual(events, [
    "recovery:start",
    "recovery:end",
    "submitA:start",
    "submitA:end",
    "submitB:start",
    "submitB:end"
  ]);
});

test("a failed transaction does not break later serialized submits", async () => {
  const queue = createSubmitTransactionQueue();
  const events = [];
  await assert.rejects(
    queue.run(async () => {
      events.push("first");
      throw new Error("boom");
    }),
    /boom/
  );
  const result = await queue.run(async () => {
    events.push("second");
    return "ok";
  });
  assert.equal(result, "ok");
  assert.deepEqual(events, ["first", "second"]);
});
