import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, statSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  MACHINE_OBSERVATION_SOURCE,
  RuntimeHostVerifier,
  WebReentryAdapter,
  machineObservationFromFrameTree,
  signHostAttestation,
  validateMachineObservation,
  verifyReentryReceipt
} from "./attestation.mjs";

const SECRET = "host-attestation-secret-0123456789abcdef0123456789abcdef";
const CONVERSATION = "6a9e4158-cc8c-83ea-9763-05a7fd51f597";

function clock(start = 1_800_000_000_000) {
  let value = start;
  return { now: () => value, add: (ms) => (value += ms) };
}

function observation(at = 1_800_000_000_100, overrides = {}) {
  return {
    host_type: "chatgpt_web",
    browser_target_id: "browser-target-7",
    top_frame_id: "frame-top-9",
    loader_id: "loader-doc-11",
    secure_origin: "https://chatgpt.com",
    conversation_id: CONVERSATION,
    observed_at_ms: at,
    source: MACHINE_OBSERVATION_SOURCE,
    ...overrides
  };
}

function verifiedFixture() {
  const time = clock();
  const verifier = new RuntimeHostVerifier(SECRET, { clock: time.now });
  const challenge = verifier.issueChallenge({ conversation_id: CONVERSATION, target_generation: 4, ownership_generation: 8 });
  time.add(100);
  const attestation = signHostAttestation({ secret: SECRET, challenge, observation: observation(time.now()), clock: time.now });
  const verified = verifier.verifyAndConsume(attestation, { conversation_id: CONVERSATION, target_generation: 4, ownership_generation: 8 });
  return { time, verifier, challenge, attestation, verified };
}

test("browser machine frame tree creates target+frame+loader bound observation", () => {
  const result = machineObservationFromFrameTree({
    tab_id: "machine-tab-1",
    observed_at_ms: 1_800_000_000_100,
    frameTree: {
      frameTree: {
        frame: {
          id: "top-frame",
          loaderId: "loader-current",
          securityOrigin: "https://chatgpt.com",
          url: `https://chatgpt.com/g/g-p-example/c/${CONVERSATION}`
        }
      }
    }
  });
  assert.equal(result.conversation_id, CONVERSATION);
  assert.equal(result.browser_target_id, "machine-tab-1");
  assert.equal(result.top_frame_id, "top-frame");
  assert.equal(result.loader_id, "loader-current");
});

test("URL-only, DOM, UI and insecure-origin observations are rejected", () => {
  for (const source of ["url_only", "dom_snapshot", "ui_observed", "prompt_claim"]) {
    assert.throws(() => validateMachineObservation(observation(undefined, { source })), /machine document provenance/);
  }
  assert.throws(() => validateMachineObservation(observation(undefined, { secure_origin: "http://chatgpt.com" })), /secure chatgpt.com/);
});

test("fresh machine attestation verifies once and consumes challenge", () => {
  const { verifier, attestation, verified } = verifiedFixture();
  assert.equal(verified.conversation_id, CONVERSATION);
  assert.equal(verified.target_generation, 4);
  assert.equal(verified.ownership_generation, 8);
  assert.throws(() => verifier.verifyAndConsume(attestation), /already consumed/);
});

test("wrong conversation and stale generations fail closed", () => {
  const time = clock();
  const verifier = new RuntimeHostVerifier(SECRET, { clock: time.now });
  const challenge = verifier.issueChallenge({ conversation_id: CONVERSATION, target_generation: 4, ownership_generation: 8 });
  time.add(100);
  const attestation = signHostAttestation({ secret: SECRET, challenge, observation: observation(time.now()), clock: time.now });
  assert.throws(() => verifier.verifyAndConsume(attestation, { conversation_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" }), /wrong Web conversation/);

  const verifier2 = new RuntimeHostVerifier(SECRET, { clock: time.now });
  const challenge2 = verifier2.issueChallenge({ conversation_id: CONVERSATION, target_generation: 4, ownership_generation: 8 });
  const attestation2 = signHostAttestation({ secret: SECRET, challenge: challenge2, observation: observation(time.now()), clock: time.now });
  assert.throws(() => verifier2.verifyAndConsume(attestation2, { target_generation: 5 }), /stale target generation/);

  const verifier3 = new RuntimeHostVerifier(SECRET, { clock: time.now });
  const challenge3 = verifier3.issueChallenge({ conversation_id: CONVERSATION, target_generation: 4, ownership_generation: 8 });
  const attestation3 = signHostAttestation({ secret: SECRET, challenge: challenge3, observation: observation(time.now()), clock: time.now });
  assert.throws(() => verifier3.verifyAndConsume(attestation3, { ownership_generation: 9 }), /stale ownership generation/);
});

test("expired challenge and forged Host MAC fail closed", () => {
  const time = clock();
  const verifier = new RuntimeHostVerifier(SECRET, { clock: time.now, challengeTtlMs: 100 });
  const challenge = verifier.issueChallenge({ conversation_id: CONVERSATION, target_generation: 1, ownership_generation: 1 });
  time.add(20);
  const attestation = signHostAttestation({ secret: SECRET, challenge, observation: observation(time.now()), clock: time.now, ttlMs: 50 });
  const forged = { ...attestation, browser_target_id: "other-target" };
  assert.throws(() => verifier.verifyAndConsume(forged), /authentication failed/);

  const verifier2 = new RuntimeHostVerifier(SECRET, { clock: time.now, challengeTtlMs: 100 });
  const challenge2 = verifier2.issueChallenge({ conversation_id: CONVERSATION, target_generation: 1, ownership_generation: 1 });
  time.add(101);
  const stale = { ...attestation, challenge_id: challenge2.challenge_id, challenge_nonce: challenge2.challenge_nonce };
  assert.throws(() => verifier2.verifyAndConsume(stale), /challenge expired/);
});

class Driver {
  constructor(current = observation()) {
    this.current = current;
    this.focused = [];
    this.submitted = [];
    this.failure = null;
    this.mutateAfterFocus = null;
    this.mutateAfterSubmit = null;
  }
  async observeTarget() {
    return this.current;
  }
  async focusTarget(target) {
    this.focused.push(target);
    if (this.mutateAfterFocus) this.current = this.mutateAfterFocus;
  }
  async submitContinuation(input) {
    this.submitted.push(input);
    if (this.failure) throw this.failure;
    if (this.mutateAfterSubmit) this.current = this.mutateAfterSubmit;
    return { dispatch_attempted: true, submit_confirmed: true, host_submit_receipt: "browser-submit-1" };
  }
}

test("same-controller re-entry checks exact target before focus, before submit and after submit", async () => {
  const { verified, time } = verifiedFixture();
  const driver = new Driver(observation(time.now()));
  const adapter = new WebReentryAdapter(SECRET, driver, { clock: time.now });
  const receipt = await adapter.reenter({
    verified_target: verified,
    wake_id: "wake-1",
    wake_nonce: "runtime-wake-nonce-1",
    continuation_payload: "continue canonical control cycle",
    expected_target_generation: 4,
    expected_ownership_generation: 8
  });
  assert.equal(receipt.dispatch_attempted, true);
  assert.equal(receipt.submit_confirmed, true);
  assert.equal(receipt.status, "submit_confirmed");
  assert.equal(driver.submitted.length, 1);
  verifyReentryReceipt(SECRET, receipt, { wake_id: "wake-1", conversation_id: CONVERSATION, target_generation: 4, ownership_generation: 8 });
  await assert.rejects(
    adapter.reenter({
      verified_target: verified,
      wake_id: "wake-1",
      wake_nonce: "other",
      continuation_payload: "duplicate",
      expected_target_generation: 4,
      expected_ownership_generation: 8
    }),
    /wake_id replayed/
  );
});

test("wrong conversation or replaced document fails before submit", async () => {
  const { verified, time } = verifiedFixture();
  const wrongConversation = new Driver(observation(time.now(), { conversation_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" }));
  const a = new WebReentryAdapter(SECRET, wrongConversation, { clock: time.now });
  await assert.rejects(
    a.reenter({ verified_target: verified, wake_id: "wake-wrong", wake_nonce: "n", continuation_payload: "x", expected_target_generation: 4, expected_ownership_generation: 8 }),
    /target changed before re-entry/
  );
  assert.equal(wrongConversation.submitted.length, 0);

  const replacedAfterFocus = new Driver(observation(time.now()));
  replacedAfterFocus.mutateAfterFocus = observation(time.now(), { loader_id: "new-document-loader" });
  const b = new WebReentryAdapter(SECRET, replacedAfterFocus, { clock: time.now });
  await assert.rejects(
    b.reenter({ verified_target: verified, wake_id: "wake-replaced", wake_nonce: "n", continuation_payload: "x", expected_target_generation: 4, expected_ownership_generation: 8 }),
    /changed after focus/
  );
  assert.equal(replacedAfterFocus.submitted.length, 0);
});

test("stale target and ownership generation fail before browser side effects", async () => {
  const { verified, time } = verifiedFixture();
  const driver = new Driver(observation(time.now()));
  const adapter = new WebReentryAdapter(SECRET, driver, { clock: time.now });
  await assert.rejects(
    adapter.reenter({ verified_target: verified, wake_id: "stale-target", wake_nonce: "n", continuation_payload: "x", expected_target_generation: 5, expected_ownership_generation: 8 }),
    /stale target generation/
  );
  await assert.rejects(
    adapter.reenter({ verified_target: verified, wake_id: "stale-owner", wake_nonce: "n", continuation_payload: "x", expected_target_generation: 4, expected_ownership_generation: 9 }),
    /stale ownership generation/
  );
  assert.equal(driver.focused.length, 0);
  assert.equal(driver.submitted.length, 0);
});

test("transient Host submit failure returns machine receipt distinct from confirmed submit", async () => {
  const { verified, time } = verifiedFixture();
  const driver = new Driver(observation(time.now()));
  const error = new Error("browser temporarily unavailable");
  error.retryable = true;
  driver.failure = error;
  const adapter = new WebReentryAdapter(SECRET, driver, { clock: time.now });
  const receipt = await adapter.reenter({
    verified_target: verified,
    wake_id: "wake-transient",
    wake_nonce: "n",
    continuation_payload: "x",
    expected_target_generation: 4,
    expected_ownership_generation: 8
  });
  assert.equal(receipt.dispatch_attempted, true);
  assert.equal(receipt.submit_confirmed, false);
  assert.equal(receipt.retryable, true);
  assert.equal(receipt.status, "transient_unavailable");
  verifyReentryReceipt(SECRET, receipt, { wake_id: "wake-transient", conversation_id: CONVERSATION, target_generation: 4, ownership_generation: 8 });
});

test("supervisor restart preserves consumed challenge and wake anti-replay state", async () => {
  const root = mkdtempSync(join(tmpdir(), "lab-host-attestation-"));
  const verifierState = join(root, "verifier.json");
  const reentryState = join(root, "reentry.json");
  const time = clock();
  const verifier = new RuntimeHostVerifier(SECRET, { clock: time.now, statePath: verifierState });
  const challenge = verifier.issueChallenge({ conversation_id: CONVERSATION, target_generation: 4, ownership_generation: 8 });
  time.add(100);
  const attestation = signHostAttestation({ secret: SECRET, challenge, observation: observation(time.now()), clock: time.now });
  const verified = verifier.verifyAndConsume(attestation, { conversation_id: CONVERSATION, target_generation: 4, ownership_generation: 8 });

  const restartedVerifier = new RuntimeHostVerifier(SECRET, { clock: time.now, statePath: verifierState });
  assert.throws(() => restartedVerifier.verifyAndConsume(attestation), /already consumed/);
  assert.equal(statSync(verifierState).mode & 0o777, 0o600);
  assert.equal(statSync(root).mode & 0o777, 0o700);

  const driver = new Driver(observation(time.now()));
  const adapter = new WebReentryAdapter(SECRET, driver, { clock: time.now, statePath: reentryState });
  await adapter.reenter({
    verified_target: verified,
    wake_id: "wake-persisted",
    wake_nonce: "persisted-nonce",
    continuation_payload: "resume",
    expected_target_generation: 4,
    expected_ownership_generation: 8
  });
  const restartedAdapter = new WebReentryAdapter(SECRET, driver, { clock: time.now, statePath: reentryState });
  await assert.rejects(
    restartedAdapter.reenter({
      verified_target: verified,
      wake_id: "wake-persisted",
      wake_nonce: "other",
      continuation_payload: "resume-again",
      expected_target_generation: 4,
      expected_ownership_generation: 8
    }),
    /wake_id replayed/
  );
  assert.equal(statSync(reentryState).mode & 0o777, 0o600);
});
