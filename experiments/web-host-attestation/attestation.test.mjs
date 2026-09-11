import test from "node:test";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { mkdtempSync, statSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  MACHINE_OBSERVATION_SOURCE,
  RuntimeHostVerifier,
  RuntimeInvocationOriginVerifier,
  ToolInvocationReceiptJournal,
  WebReentryAdapter,
  machineObservationFromFrameTree,
  signHostAttestation,
  signRuntimeInvocationOriginReceipt,
  validateMachineObservation,
  validateMachineInvocationOrigin,
  verifyReentryReceipt
} from "./attestation.mjs";
import { createHostDriver } from "./host-driver.mjs";
import { createRuntimeHostProvider } from "./provider.mjs";

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

test("attestation envelope browser target must exactly match machine observation", () => {
  const time = clock();
  const verifier = new RuntimeHostVerifier(SECRET, { clock: time.now });
  const challenge = verifier.issueChallenge({ conversation_id: CONVERSATION, target_generation: 4, ownership_generation: 8 });
  time.add(100);
  const attestation = signHostAttestation({ secret: SECRET, challenge, observation: observation(time.now()), clock: time.now });
  const mismatched = { ...attestation, browser_target_id: "different-machine-target" };
  const unsigned = { ...mismatched };
  delete unsigned.host_mac_sha256;
  // A Host signer could produce a self-authenticating but internally inconsistent envelope.
  const resigned = signHostAttestationForTest(unsigned);
  assert.throws(() => verifier.verifyAndConsume(resigned), /browser target mismatch/);
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

function signHostAttestationForTest(unsigned) {
  return { ...unsigned, host_mac_sha256: createHmac("sha256", SECRET).update(canonicalJsonForTest(unsigned)).digest("hex") };
}

function canonicalJsonForTest(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJsonForTest).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJsonForTest(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

test("concurrent same-wake reentry admits only one submit", async () => {
  const { verified, time } = verifiedFixture();
  let releaseObserve;
  const observeGate = new Promise((resolve) => {
    releaseObserve = resolve;
  });
  const driver = new Driver(observation(time.now()));
  driver.observeTarget = async function () {
    await observeGate;
    return this.current;
  };
  const adapter = new WebReentryAdapter(SECRET, driver, { clock: time.now });
  const input = {
    verified_target: verified,
    wake_id: "wake-concurrent",
    wake_nonce: "nonce-concurrent",
    continuation_payload: "continue",
    expected_target_generation: 4,
    expected_ownership_generation: 8
  };

  const first = adapter.reenter(input);
  const second = adapter.reenter(input);
  releaseObserve();

  const settled = await Promise.allSettled([first, second]);
  assert.equal(settled.filter((item) => item.status === "fulfilled").length, 1);
  assert.equal(settled.filter((item) => item.status === "rejected").length, 1);
  const rejected = settled.find((item) => item.status === "rejected");
  assert.match(String(rejected.reason && rejected.reason.message || rejected.reason), /wake_id replayed/);
  assert.equal(driver.submitted.length, 1);
});

test("forged verified target provenance string cannot authorize re-entry", async () => {
  const { verified, time } = verifiedFixture();
  const driver = new Driver(observation(time.now()));
  const adapter = new WebReentryAdapter(SECRET, driver, { clock: time.now });
  const forged = {
    ...verified,
    verified_target_id: "vt_forged",
    target_generation: 4,
    ownership_generation: 8
  };
  delete forged.verifier_mac_sha256;
  await assert.rejects(
    adapter.reenter({
      verified_target: forged,
      wake_id: "wake-forged-target",
      wake_nonce: "nonce",
      continuation_payload: "resume",
      expected_target_generation: 4,
      expected_ownership_generation: 8
    }),
    /verified target authentication failed/
  );
  assert.equal(driver.submitted.length, 0);
});

test("pre-dispatch target observation failure does not consume the wake", async () => {
  const { verified, time } = verifiedFixture();
  const driver = new Driver(observation(time.now(), { loader_id: "wrong-loader" }));
  const adapter = new WebReentryAdapter(SECRET, driver, { clock: time.now });
  const input = {
    verified_target: verified,
    wake_id: "wake-precheck-retry",
    wake_nonce: "nonce",
    continuation_payload: "resume",
    expected_target_generation: 4,
    expected_ownership_generation: 8
  };
  const first = await adapter.reenter(input);
  assert.equal(first.result_class, "CONFIRMED_FAILURE_BEFORE_DISPATCH");
  assert.equal(first.dispatch_attempted, false);
  driver.current = observation(time.now());
  const second = await adapter.reenter(input);
  assert.equal(second.result_class, "SUBMIT_CONFIRMED");
  assert.equal(driver.submitted.length, 1);
});

test("post-submit observation exception becomes signed RESULT_UNKNOWN", async () => {
  const { verified, time } = verifiedFixture();
  const driver = new Driver(observation(time.now()));
  let observes = 0;
  driver.observeTarget = async function () {
    observes += 1;
    if (observes > 1) throw new Error("post-submit observation unavailable");
    return this.current;
  };
  const receipt = await new WebReentryAdapter(SECRET, driver, { clock: time.now }).reenter({
    verified_target: verified,
    wake_id: "wake-post-observe-throw",
    wake_nonce: "nonce",
    continuation_payload: "resume",
    expected_target_generation: 4,
    expected_ownership_generation: 8
  });
  assert.equal(receipt.result_class, "RESULT_UNKNOWN");
  assert.equal(receipt.dispatch_attempted, true);
  assert.equal(receipt.auto_retry_allowed, false);
});

test("resolved submit with missing or contradictory dispatch flags is RESULT_UNKNOWN", async () => {
  const { verified, time } = verifiedFixture();
  for (const [name, submitResult] of [
    ["missing", {}],
    ["confirmed_without_dispatch", { dispatch_attempted: false, submit_confirmed: true }]
  ]) {
    const driver = new Driver(observation(time.now()));
    driver.submitContinuation = async function (input) {
      this.submitted.push(input);
      return submitResult;
    };
    const receipt = await new WebReentryAdapter(SECRET, driver, { clock: time.now }).reenter({
      verified_target: verified,
      wake_id: "wake-invalid-flags-" + name,
      wake_nonce: "nonce",
      continuation_payload: "resume",
      expected_target_generation: 4,
      expected_ownership_generation: 8
    });
    assert.equal(receipt.result_class, "RESULT_UNKNOWN");
    assert.equal(receipt.dispatch_attempted, true);
    assert.equal(receipt.submit_confirmed, false);
    assert.equal(receipt.auto_retry_allowed, false);
  }
});

test("same-controller re-entry checks exact target before and after submit without foreground focus", async () => {
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
  verifyReentryReceipt(SECRET, receipt, {
    wake_id: "wake-1",
    wake_nonce: "runtime-wake-nonce-1",
    continuation_payload: "continue canonical control cycle",
    conversation_id: CONVERSATION,
    browser_target_id: "browser-target-7",
    target_generation: 4,
    ownership_generation: 8
  });
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
  const wrongReceipt = await a.reenter({
    verified_target: verified,
    wake_id: "wake-wrong",
    wake_nonce: "n",
    continuation_payload: "x",
    expected_target_generation: 4,
    expected_ownership_generation: 8
  });
  assert.equal(wrongReceipt.result_class, "CONFIRMED_FAILURE_BEFORE_DISPATCH");
  assert.equal(wrongReceipt.status, "target_changed_before_reentry");
  assert.equal(wrongReceipt.dispatch_attempted, false);
  assert.equal(wrongReceipt.auto_retry_allowed, false);
  assert.equal(wrongConversation.submitted.length, 0);

  const noFocusDriver = new Driver(observation(time.now()));
  noFocusDriver.mutateAfterFocus = observation(time.now(), { loader_id: "new-document-loader" });
  const b = new WebReentryAdapter(SECRET, noFocusDriver, { clock: time.now });
  const receipt = await b.reenter({
    verified_target: verified,
    wake_id: "wake-no-focus",
    wake_nonce: "n",
    continuation_payload: "x",
    expected_target_generation: 4,
    expected_ownership_generation: 8
  });
  assert.equal(receipt.submit_confirmed, true);
  assert.equal(noFocusDriver.focused.length, 0);
  assert.equal(noFocusDriver.submitted.length, 1);
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
  error.dispatch_attempted = false;
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
  assert.equal(receipt.dispatch_attempted, false);
  assert.equal(receipt.submit_confirmed, false);
  assert.equal(receipt.retryable, true);
  assert.equal(receipt.status, "transient_unavailable");
  assert.equal(receipt.result_class, "CONFIRMED_FAILURE_BEFORE_DISPATCH");
  assert.equal(receipt.auto_retry_allowed, true);
  verifyReentryReceipt(SECRET, receipt, {
    wake_id: "wake-transient",
    wake_nonce: "n",
    continuation_payload: "x",
    conversation_id: CONVERSATION,
    browser_target_id: "browser-target-7",
    target_generation: 4,
    ownership_generation: 8
  });
});

test("submit uncertainty and post-submit target verification failure are RESULT_UNKNOWN and never auto-retry", async () => {
  const { verified, time } = verifiedFixture();
  const uncertainDriver = new Driver(observation(time.now()));
  const uncertainError = new Error("transport disconnected during submit");
  uncertainError.retryable = true;
  uncertainDriver.failure = uncertainError;
  const uncertain = await new WebReentryAdapter(SECRET, uncertainDriver, { clock: time.now }).reenter({
    verified_target: verified,
    wake_id: "wake-uncertain",
    wake_nonce: "n",
    continuation_payload: "x",
    expected_target_generation: 4,
    expected_ownership_generation: 8
  });
  assert.equal(uncertain.dispatch_attempted, true);
  assert.equal(uncertain.result_class, "RESULT_UNKNOWN");
  assert.equal(uncertain.status, "result_unknown");
  assert.equal(uncertain.retryable, false);
  assert.equal(uncertain.auto_retry_allowed, false);

  const changedDriver = new Driver(observation(time.now()));
  changedDriver.mutateAfterSubmit = observation(time.now(), { loader_id: "post-submit-loader" });
  const changed = await new WebReentryAdapter(SECRET, changedDriver, { clock: time.now }).reenter({
    verified_target: verified,
    wake_id: "wake-post-submit-change",
    wake_nonce: "n",
    continuation_payload: "x",
    expected_target_generation: 4,
    expected_ownership_generation: 8
  });
  assert.equal(changed.result_class, "RESULT_UNKNOWN");
  assert.equal(changed.status, "result_unknown");
  assert.equal(changed.auto_retry_allowed, false);
});

test("stable host driver consumes machine-boundary frame observations and validates calls", async () => {
  const calls = [];
  const driver = createHostDriver({
    observeTarget: async (targetId) => ({
      tab_id: targetId,
      observed_at_ms: 1_800_000_000_100,
      frameTree: { frameTree: { frame: { id: "top", loaderId: "loader", securityOrigin: "https://chatgpt.com", url: `https://chatgpt.com/c/${CONVERSATION}` } } }
    }),
    focusTarget: async (targetId) => calls.push(["focus", targetId]),
    submitContinuation: async (input) => ({ dispatch_attempted: true, submit_confirmed: true, host_submit_receipt: `submitted:${input.wake_id}` })
  });
  const observed = await driver.observeTarget("browser-target-7");
  assert.equal(observed.browser_target_id, "browser-target-7");
  assert.equal(observed.top_frame_id, "top");
  assert.deepEqual(calls, []);
  await assert.rejects(() => driver.observeTarget(""), /browser_target_id is required/);
  await assert.rejects(() => driver.submitContinuation({
    browser_target_id: "browser-target-7",
    conversation_id: CONVERSATION,
    payload: "",
    wake_id: "w",
    wake_nonce: "n",
    target_generation: 4,
    ownership_generation: 8
  }), /payload is required/);
  assert.throws(() => createHostDriver({ observeTarget() {} }), /submitContinuation/);
});

test("stable host driver strips sensitive submit diagnostics before signed receipt", async () => {
  const driver = createHostDriver({
    observeTarget: async targetId => observation(1_800_000_000_100, {
      browser_target_id: targetId
    }),
    submitContinuation: async input => ({
      dispatch_attempted: true,
      submit_confirmed: true,
      retryable: false,
      status: "submit_confirmed",
      host_submit_receipt: "receipt:" + input.wake_id,
      submit_diagnostics: {
        schema_version: 1,
        wake_id: input.wake_id,
        browser_target_id: input.browser_target_id,
        phase: "confirmed",
        debugger_attach: { attempted: true, ok: true },
        composer_preparation: {
          attempted: true,
          ok: true,
          status: "prepared",
          rollback_attempted: false,
          rollback_succeeded: false
        },
        send_control_resolution: {
          attempted: true,
          ok: true,
          selector_kind: "testid_send_button",
          coordinates_changed_since_prepare: false
        },
        trusted_dispatch: {
          attempted: true,
          focus_emulation: true,
          mouse_moved: true,
          mouse_pressed: true,
          mouse_released: true
        },
        confirmation: {
          attempted: true,
          ok: true,
          composer_empty: true,
          exact_new_user_message: true
        },
        foreground: {
          mode: "background_focus_emulation",
          activated_target: false,
          restored_previous_active: false,
          user_changed_focus: false
        },
        failure_stage: null,
        dispatch_attempted: true,
        submit_confirmed: true,
        retryable: false,
        payload: "MUST_NOT_ESCAPE",
        continuation_payload: "MUST_NOT_ESCAPE",
        text: "MUST_NOT_ESCAPE"
      }
    })
  });
  const receipt = await driver.submitContinuation({
    browser_target_id: "browser-target-7",
    conversation_id: CONVERSATION,
    payload: "sensitive body",
    wake_id: "wake-driver-diag",
    wake_nonce: "nonce-driver-diag",
    target_generation: 4,
    ownership_generation: 8
  });
  assert.equal(receipt.submit_diagnostics.debugger_attach.ok, true);
  const serialized = JSON.stringify(receipt.submit_diagnostics);
  assert.equal(serialized.includes("MUST_NOT_ESCAPE"), false);
  assert.equal(serialized.includes("sensitive body"), false);
});

test("stable provider exposes receipts without owning Runtime lifecycle", async () => {
  const time = clock();
  const rawDriver = new Driver(observation(time.now()));
  const provider = createRuntimeHostProvider({ secret: SECRET, driver: rawDriver, clock: time.now });
  assert.deepEqual(provider.capabilities, ["issueChallenge", "verifyAttestation", "submitReentry"]);
  assert.equal("wake" in provider, false);
  assert.equal("retry" in provider, false);
  const challenge = provider.issueChallenge({ conversation_id: CONVERSATION, target_generation: 4, ownership_generation: 8 });
  time.add(100);
  const attestation = signHostAttestation({ secret: SECRET, challenge, observation: observation(time.now()), clock: time.now });
  const verified = provider.verifyAttestation(attestation, { conversation_id: CONVERSATION, target_generation: 4, ownership_generation: 8 });
  const receipt = await provider.submitReentry({ verified_target: verified, wake_id: "provider-wake", wake_nonce: "n", continuation_payload: "resume", expected_target_generation: 4, expected_ownership_generation: 8 });
  assert.equal(receipt.result_class, "SUBMIT_CONFIRMED");
});

test("receipt verifier binds browser target nonce payload schema and typed generations", async () => {
  const { verified, time } = verifiedFixture();
  const driver = new Driver(observation(time.now()));
  const receipt = await new WebReentryAdapter(SECRET, driver, { clock: time.now }).reenter({
    verified_target: verified,
    wake_id: "wake-full-binding",
    wake_nonce: "nonce-full-binding",
    continuation_payload: "resume-full-binding",
    expected_target_generation: 4,
    expected_ownership_generation: 8
  });
  const expected = {
    wake_id: "wake-full-binding",
    wake_nonce: "nonce-full-binding",
    continuation_payload: "resume-full-binding",
    conversation_id: CONVERSATION,
    browser_target_id: "browser-target-7",
    target_generation: 4,
    ownership_generation: 8
  };
  verifyReentryReceipt(SECRET, receipt, expected);
  assert.throws(() => verifyReentryReceipt(SECRET, receipt, { ...expected, browser_target_id: "other-target" }), /wrong browser target receipt/);
  assert.throws(() => verifyReentryReceipt(SECRET, receipt, { ...expected, wake_nonce: "other-nonce" }), /wake nonce hash mismatch/);
  assert.throws(() => verifyReentryReceipt(SECRET, receipt, { ...expected, continuation_payload: "other-payload" }), /continuation payload hash mismatch/);

  for (const mutation of [
    { schema_version: 2 },
    { target_generation: 0 },
    { ownership_generation: 0 },
    { result_class: "UNKNOWN_NEW_CLASS" }
  ]) {
    const unsigned = { ...receipt, ...mutation };
    delete unsigned.host_mac_sha256;
    const resigned = signHostAttestationForTest(unsigned);
    assert.throws(() => verifyReentryReceipt(SECRET, resigned, expected));
  }
});

test("receipt verifier rejects authenticated contradictory result state", async () => {
  const { verified, time } = verifiedFixture();
  const driver = new Driver(observation(time.now()));
  const receipt = await new WebReentryAdapter(SECRET, driver, { clock: time.now }).reenter({
    verified_target: verified,
    wake_id: "wake-contradiction",
    wake_nonce: "n",
    continuation_payload: "resume",
    expected_target_generation: 4,
    expected_ownership_generation: 8
  });
  const unsigned = { ...receipt, result_class: "RESULT_UNKNOWN", auto_retry_allowed: false };
  delete unsigned.host_mac_sha256;
  const resigned = signHostAttestationForTest(unsigned);
  assert.throws(
    () => verifyReentryReceipt(SECRET, resigned, {
      wake_id: "wake-contradiction",
      wake_nonce: "n",
      continuation_payload: "resume",
      conversation_id: CONVERSATION,
      browser_target_id: "browser-target-7",
      target_generation: 4,
      ownership_generation: 8
    }),
    /result-unknown state is inconsistent/
  );
});

test("verified Host target receipt persists across verifier process restart and is one-shot", () => {
  const root = mkdtempSync(join(tmpdir(), "lab-host-verified-target-"));
  const verifierState = join(root, "verifier.json");
  const time = clock();
  const verifier = new RuntimeHostVerifier(SECRET, { clock: time.now, statePath: verifierState });
  const challenge = verifier.issueChallenge({
    conversation_id: CONVERSATION,
    target_generation: 5,
    ownership_generation: 9
  });
  time.add(100);
  const attestation = signHostAttestation({
    secret: SECRET,
    challenge,
    observation: observation(time.now()),
    clock: time.now
  });
  const verified = verifier.verifyAndConsume(attestation, {
    conversation_id: CONVERSATION,
    target_generation: 5,
    ownership_generation: 9
  });
  const restarted = new RuntimeHostVerifier(SECRET, { clock: time.now, statePath: verifierState });
  assert.deepEqual(
    restarted.consumeVerifiedTarget(attestation.host_receipt_id, {
      conversation_id: CONVERSATION,
      target_generation: 5,
      ownership_generation: 9
    }),
    verified
  );
  assert.throws(
    () => restarted.consumeVerifiedTarget(attestation.host_receipt_id, {
      conversation_id: CONVERSATION,
      target_generation: 5,
      ownership_generation: 9
    }),
    /missing or already consumed/
  );
});

test("restart converts persisted in-flight wake into RESULT_UNKNOWN without a second submit", async () => {
  const root = mkdtempSync(join(tmpdir(), "lab-host-inflight-restart-"));
  const reentryState = join(root, "reentry.json");
  const { verified, time } = verifiedFixture();
  let releaseSubmit;
  const submitGate = new Promise((resolve) => { releaseSubmit = resolve; });
  const firstDriver = new Driver(observation(time.now()));
  firstDriver.submitContinuation = async function (input) {
    this.submitted.push(input);
    await submitGate;
    const error = new Error("simulated old process termination");
    error.dispatch_attempted = true;
    throw error;
  };
  const input = {
    verified_target: verified,
    wake_id: "wake-inflight-restart",
    wake_nonce: "nonce-inflight",
    continuation_payload: "resume",
    expected_target_generation: 4,
    expected_ownership_generation: 8
  };
  const first = new WebReentryAdapter(SECRET, firstDriver, { clock: time.now, statePath: reentryState }).reenter(input);
  await new Promise((resolve) => setImmediate(resolve));

  const restartedDriver = new Driver(observation(time.now()));
  const restarted = new WebReentryAdapter(SECRET, restartedDriver, { clock: time.now, statePath: reentryState });
  const recovered = await restarted.reenter(input);
  assert.equal(recovered.result_class, "RESULT_UNKNOWN");
  assert.equal(recovered.dispatch_attempted, true);
  assert.equal(recovered.auto_retry_allowed, false);
  assert.equal(restartedDriver.submitted.length, 0);

  releaseSubmit();
  await first;
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

test("signed re-entry receipt carries structured submit diagnostics without prompt body", async () => {
  const { verified, time } = verifiedFixture();
  const driver = new Driver(observation(time.now()));
  driver.submitContinuation = async function (input) {
    this.submitted.push(input);
    return {
      dispatch_attempted: true,
      submit_confirmed: true,
      retryable: false,
      status: "submit_confirmed",
      host_submit_receipt: "browser-submit-diag",
      submit_diagnostics: {
        schema_version: 1,
        wake_id: input.wake_id,
        browser_target_id: input.browser_target_id,
        phase: "confirmed",
        debugger_attach: { attempted: true, ok: true },
        composer_preparation: {
          attempted: true,
          ok: true,
          status: "prepared",
          rollback_attempted: false,
          rollback_succeeded: false
        },
        send_control_resolution: {
          attempted: true,
          ok: true,
          selector_kind: "testid_send_button",
          coordinates_changed_since_prepare: false
        },
        trusted_dispatch: {
          attempted: true,
          focus_emulation: true,
          mouse_moved: true,
          mouse_pressed: true,
          mouse_released: true
        },
        confirmation: {
          attempted: true,
          ok: true,
          composer_empty: true,
          exact_new_user_message: true
        },
        foreground: {
          mode: "background_focus_emulation",
          activated_target: false,
          restored_previous_active: false,
          user_changed_focus: false
        },
        failure_stage: null,
        dispatch_attempted: true,
        submit_confirmed: true,
        retryable: false
      }
    };
  };
  const receipt = await new WebReentryAdapter(SECRET, driver, { clock: time.now }).reenter({
    verified_target: verified,
    wake_id: "wake-diag",
    wake_nonce: "nonce-diag",
    continuation_payload: "sensitive continuation body",
    expected_target_generation: 4,
    expected_ownership_generation: 8
  });
  const verifiedReceipt = verifyReentryReceipt(SECRET, receipt, {
    wake_id: "wake-diag",
    wake_nonce: "nonce-diag",
    continuation_payload: "sensitive continuation body",
    conversation_id: CONVERSATION,
    browser_target_id: "browser-target-7",
    target_generation: 4,
    ownership_generation: 8
  });
  assert.equal(verifiedReceipt.submit_diagnostics.debugger_attach.ok, true);
  assert.equal(verifiedReceipt.submit_diagnostics.confirmation.exact_new_user_message, true);
  assert.equal(JSON.stringify(verifiedReceipt.submit_diagnostics).includes("sensitive continuation body"), false);
});


const INVOCATION_ID = "f98bbb53-3c0e-480b-814a-e0c1f75c98c6/3htx";

function invocationOrigin(at = 1_800_000_000_100, overrides = {}) {
  return {
    host_type: "chatgpt_web",
    invocation_id: INVOCATION_ID,
    operation: "controller_privileged_mutation",
    browser_target_id: "browser-target-origin-1",
    top_frame_id: "frame-origin-1",
    loader_id: "loader-origin-1",
    secure_origin: "https://chatgpt.com",
    conversation_id: CONVERSATION,
    observed_at_ms: at,
    source: "browser_cdp_invocation_origin_v1",
    ...overrides
  };
}

test("runtime invocation origin challenge never accepts or carries caller conversation identity", () => {
  const time = clock();
  const verifier = new RuntimeInvocationOriginVerifier(SECRET, {
    clock: time.now,
    hostEpoch: "host-epoch-a"
  });
  const challenge = verifier.issueChallenge({
    invocation_id: INVOCATION_ID,
    operation: "controller_privileged_mutation",
    target_generation: 7,
    ownership_generation: 7,
    conversation_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
  });
  assert.equal("conversation_id" in challenge, false);
  assert.equal(challenge.invocation_id, INVOCATION_ID);
  assert.equal(challenge.operation, "controller_privileged_mutation");
});

test("runtime invocation origin receipt binds machine origin and verifies exactly once", () => {
  const time = clock();
  const verifier = new RuntimeInvocationOriginVerifier(SECRET, {
    clock: time.now,
    hostEpoch: "host-epoch-a"
  });
  const challenge = verifier.issueChallenge({
    invocation_id: INVOCATION_ID,
    operation: "controller_privileged_mutation",
    target_generation: 7,
    ownership_generation: 7
  });
  time.add(50);
  const receipt = signRuntimeInvocationOriginReceipt({
    secret: SECRET,
    challenge,
    machineOrigin: invocationOrigin(time.now()),
    hostEpoch: "host-epoch-a",
    clock: time.now
  });
  const verified = verifier.verifyAndConsume(receipt, {
    invocation_id: INVOCATION_ID,
    operation: "controller_privileged_mutation",
    conversation_id: CONVERSATION,
    target_generation: 7,
    ownership_generation: 7
  });
  assert.equal(verified.provenance, "runtime_invocation_origin_v1");
  assert.equal(verified.conversation_id, CONVERSATION);
  assert.equal(verified.browser_target_id, "browser-target-origin-1");
  assert.equal(verified.top_frame_id, "frame-origin-1");
  assert.equal(verified.loader_id, "loader-origin-1");
  assert.equal(verified.invocation_id, INVOCATION_ID);
  assert.equal(verified.operation, "controller_privileged_mutation");
  assert.throws(() => verifier.verifyAndConsume(receipt), /already consumed|replay/i);
});

test("runtime invocation origin rejects foreign conversation operation generation and forged machine source", () => {
  const cases = [
    ["conversation", { expected: { conversation_id: "bbbbbbbb-cccc-dddd-eeee-ffffffffffff" }, pattern: /conversation/i }],
    ["operation", { expected: { operation: "different_operation" }, pattern: /operation/i }],
    ["target generation", { expected: { target_generation: 8 }, pattern: /target generation/i }],
    ["ownership generation", { expected: { ownership_generation: 8 }, pattern: /ownership generation/i }]
  ];
  for (const [name, spec] of cases) {
    const time = clock();
    const verifier = new RuntimeInvocationOriginVerifier(SECRET, { clock: time.now, hostEpoch: "host-epoch-a" });
    const challenge = verifier.issueChallenge({
      invocation_id: INVOCATION_ID,
      operation: "controller_privileged_mutation",
      target_generation: 7,
      ownership_generation: 7
    });
    time.add(10);
    const receipt = signRuntimeInvocationOriginReceipt({
      secret: SECRET,
      challenge,
      machineOrigin: invocationOrigin(time.now()),
      hostEpoch: "host-epoch-a",
      clock: time.now
    });
    assert.throws(() => verifier.verifyAndConsume(receipt, spec.expected), spec.pattern, name);
  }

  assert.throws(
    () => validateMachineInvocationOrigin(invocationOrigin(undefined, { source: "caller_supplied_conversation" })),
    /machine invocation provenance/i
  );
});

test("runtime invocation origin receipt expires and dies across Host epoch restart", () => {
  const time = clock();
  const originStateDir = mkdtempSync(join(tmpdir(), "lab-invocation-origin-state-"));
  const originStatePath = join(originStateDir, "state.json");
  const first = new RuntimeInvocationOriginVerifier(SECRET, {
    clock: time.now,
    hostEpoch: "host-epoch-a",
    challengeTtlMs: 100,
    receiptTtlMs: 50,
    statePath: originStatePath
  });
  const challenge = first.issueChallenge({
    invocation_id: INVOCATION_ID,
    operation: "controller_privileged_mutation",
    target_generation: 7,
    ownership_generation: 7
  });
  time.add(10);
  const receipt = signRuntimeInvocationOriginReceipt({
    secret: SECRET,
    challenge,
    machineOrigin: invocationOrigin(time.now()),
    hostEpoch: "host-epoch-a",
    clock: time.now,
    ttlMs: 50
  });

  const restarted = new RuntimeInvocationOriginVerifier(SECRET, {
    clock: time.now,
    hostEpoch: "host-epoch-b",
    statePath: originStatePath
  });
  assert.throws(() => restarted.verifyAndConsume(receipt), /Host epoch/i);

  time.add(60);
  assert.throws(() => first.verifyAndConsume(receipt), /expired/i);
});

test("two concurrent Web conversation receipts cannot cross-consume", () => {
  const time = clock();
  const verifier = new RuntimeInvocationOriginVerifier(SECRET, { clock: time.now, hostEpoch: "host-epoch-a" });
  const c1 = verifier.issueChallenge({ invocation_id: "invocation-a", operation: "op-a", target_generation: 7, ownership_generation: 7 });
  const c2 = verifier.issueChallenge({ invocation_id: "invocation-b", operation: "op-a", target_generation: 7, ownership_generation: 7 });
  time.add(10);
  const r1 = signRuntimeInvocationOriginReceipt({
    secret: SECRET,
    challenge: c1,
    machineOrigin: invocationOrigin(time.now(), { invocation_id: "invocation-a", operation: "op-a", conversation_id: CONVERSATION }),
    hostEpoch: "host-epoch-a",
    clock: time.now
  });
  const foreign = "cccccccc-dddd-eeee-ffff-000000000000";
  const r2 = signRuntimeInvocationOriginReceipt({
    secret: SECRET,
    challenge: c2,
    machineOrigin: invocationOrigin(time.now(), { invocation_id: "invocation-b", operation: "op-a", conversation_id: foreign, browser_target_id: "browser-target-origin-2" }),
    hostEpoch: "host-epoch-a",
    clock: time.now
  });
  assert.throws(() => verifier.verifyAndConsume(r1, { invocation_id: "invocation-b" }), /invocation/i);
  assert.throws(() => verifier.verifyAndConsume(r2, { conversation_id: CONVERSATION }), /conversation/i);
});

function receiptMachineContext(overrides = {}) {
  return {
    conversation_id: CONVERSATION,
    browser_target_id: "browser-target-7",
    top_frame_id: "frame-top-9",
    loader_id: "loader-doc-11",
    secure_origin: "https://chatgpt.com",
    generation_anchor_sha256: "a".repeat(64),
    extension_binding_sha256: "b".repeat(64),
    extension_instance_id_sha256: "c".repeat(64),
    extension_loaded_at_ms: 1_800_000_000_000,
    ...overrides
  };
}

function receiptRequest(overrides = {}) {
  return {
    bridge_call_id: "bc_test-call",
    bridge_challenge_nonce: "bridge-challenge-nonce-test",
    transport_binding_id: "tb_test-transport",
    workspace: "/Users/echoman/Documents/ChatGPT/Local-Agent-Bridge",
    production_release_revision: "a".repeat(40),
    production_endpoint_generation: 7,
    bridge_principal_id: "bridge-principal-test",
    bridge_execution_session_id: "bridge-execution-session-test",
    bridge_capability_id: "bridge-capability-test",
    bridge_workspace_id: "local-agent-bridge-production",
    tool_name: "run_command",
    outer_request_sha256: "d".repeat(64),
    forwarded_request_sha256: "e".repeat(64),
    normalized_request_sha256: "f".repeat(64),
    machine_context: receiptMachineContext(),
    ...overrides
  };
}

const CAPABILITY_SCHEMA_KEYS = [
  "schema_version", "provenance", "capability_id", "bridge_call_id", "bridge_challenge_nonce",
  "transport_binding_id", "workspace", "production_release_revision", "production_endpoint_generation",
  "bridge_principal_id", "bridge_execution_session_id", "bridge_capability_id", "bridge_workspace_id",
  "tool_name", "outer_request_sha256", "forwarded_request_sha256", "normalized_request_sha256",
  "conversation_id", "browser_target_id", "top_frame_id", "loader_id", "generation_anchor_sha256",
  "secure_origin", "extension_binding_sha256", "extension_instance_id_sha256", "extension_loaded_at_ms",
  "issued_at_unix_ms", "expires_at_unix_ms", "host_mac_sha256"
].sort();

const PRE_SCHEMA_KEYS = [
  "schema_version", "provenance", "pre_receipt_id", "capability_id", "host_tool_execution_id",
  "bridge_call_id", "bridge_challenge_nonce", "transport_binding_id", "workspace",
  "production_release_revision", "production_endpoint_generation", "bridge_principal_id",
  "bridge_execution_session_id", "bridge_capability_id", "bridge_workspace_id", "tool_name",
  "outer_request_sha256", "forwarded_request_sha256", "normalized_request_sha256", "conversation_id",
  "browser_target_id", "top_frame_id", "loader_id", "generation_anchor_sha256", "secure_origin",
  "extension_binding_sha256", "extension_instance_id_sha256", "extension_loaded_at_ms", "pre_nonce",
  "issued_at_unix_ms", "host_epoch_binding_sha256", "host_mac_sha256"
].sort();

const TERMINAL_SCHEMA_KEYS = [
  "schema_version", "provenance", "terminal_receipt_id", "pre_receipt_id", "pre_receipt_sha256",
  "capability_id", "host_tool_execution_id", "bridge_call_id", "bridge_challenge_nonce",
  "transport_binding_id", "workspace", "production_release_revision", "production_endpoint_generation",
  "bridge_principal_id", "bridge_execution_session_id", "bridge_capability_id", "bridge_workspace_id",
  "tool_name", "outer_request_sha256", "forwarded_request_sha256", "normalized_request_sha256",
  "conversation_id", "browser_target_id", "top_frame_id", "loader_id", "generation_anchor_sha256",
  "secure_origin", "extension_binding_sha256", "extension_instance_id_sha256", "extension_loaded_at_ms",
  "backend_route", "http_status", "response_body_sha256", "terminal_classification", "issued_at_unix_ms",
  "host_epoch_binding_sha256", "host_mac_sha256"
].sort();

test("durable Host receipt journal binds only machine identity and exact request across restart", () => {
  const time = clock();
  const statePath = join(mkdtempSync(join(tmpdir(), "lab-tool-receipt-")), "receipts.json");
  const journal = new ToolInvocationReceiptJournal(SECRET, {
    clock: time.now,
    statePath,
    capabilityTtlMs: 100
  });
  const capability = journal.issueCapability({
    ...receiptRequest(),
    conversation_id: "caller-forged-conversation",
    browser_target_id: "caller-forged-target",
    caller_identity: "not-evidence"
  });
  assert.equal(capability.provenance, "lab_host_invocation_capability_v1");
  assert.deepEqual(Object.keys(capability).sort(), CAPABILITY_SCHEMA_KEYS);
  assert.equal(capability.conversation_id, CONVERSATION);
  assert.equal(Object.hasOwn(capability, "caller_identity"), false);
  assert.equal(Object.hasOwn(capability, "generation_request_ids"), false);

  const restarted = new ToolInvocationReceiptJournal(SECRET, { clock: time.now, statePath, capabilityTtlMs: 100 });
  assert.throws(
    () => restarted.consumeCapability({
      ...receiptRequest({ transport_binding_id: "tb_other" }),
      capability,
      host_tool_execution_id: "hte_test-execution"
    }),
    /mismatch/i
  );

  const pre = restarted.consumeCapability({
    ...receiptRequest(),
    capability,
    host_tool_execution_id: "hte_test-execution"
  });
  assert.equal(pre.provenance, "lab_host_tool_pre_receipt_v1");
  assert.deepEqual(Object.keys(pre).sort(), PRE_SCHEMA_KEYS);
  assert.throws(
    () => restarted.consumeCapability({
      ...receiptRequest(),
      capability,
      host_tool_execution_id: "hte_test-execution"
    }),
    /replay|mismatch/i
  );

  assert.throws(
    () => restarted.terminalize({
      ...receiptRequest(),
      pre_receipt: { ...pre, pre_receipt_id: "hpr_wrong" },
      backend_route: "/v1/tools/run_command",
      http_status: 200,
      response_body_sha256: "1".repeat(64),
      terminal_classification: "SUCCESS"
    }),
    /authentication|pre.*mismatch|mismatch.*pre/i
  );
  const terminal = restarted.terminalize({
    ...receiptRequest(),
    pre_receipt: pre,
    backend_route: "/v1/tools/run_command",
    http_status: 200,
    response_body_sha256: "1".repeat(64),
    terminal_classification: "SUCCESS",
    response_body: "must never persist"
  });
  assert.equal(terminal.provenance, "lab_host_tool_terminal_receipt_v1");
  assert.deepEqual(Object.keys(terminal).sort(), TERMINAL_SCHEMA_KEYS);
  assert.equal(JSON.stringify(restarted.inspectStateForTest()).includes("must never persist"), false);
});

test("Host receipt journal rejects expiry and every re-observed machine drift", () => {
  const time = clock();
  const journal = new ToolInvocationReceiptJournal(SECRET, { clock: time.now, capabilityTtlMs: 10 });
  const capability = journal.issueCapability(receiptRequest());
  for (const field of [
    "conversation_id", "browser_target_id", "top_frame_id", "loader_id", "generation_anchor_sha256",
    "secure_origin", "extension_binding_sha256", "extension_instance_id_sha256", "extension_loaded_at_ms"
  ]) {
    const changedValue = field === "conversation_id"
      ? "7b9e4158-cc8c-83ea-9763-05a7fd51f597"
      : field === "secure_origin"
        ? "https://chatgpt.com:443"
        : field === "extension_loaded_at_ms"
          ? 1_800_000_000_001
          : field.includes("sha256")
            ? "9".repeat(64)
            : "changed-" + field;
    const changed = receiptMachineContext({ [field]: changedValue });
    assert.throws(
      () => journal.consumeCapability({
        ...receiptRequest({ machine_context: changed }), capability, host_tool_execution_id: "hte_" + field
      }),
      /mismatch|secure origin/i,
      field
    );
  }
  time.add(10);
  assert.throws(
    () => journal.consumeCapability({ ...receiptRequest(), capability, host_tool_execution_id: "hte_expired" }),
    /expired/i
  );
});

test("two receipt journal instances permit exactly one consume after reloading durable state", () => {
  const time = clock();
  const statePath = join(mkdtempSync(join(tmpdir(), "lab-tool-receipt-race-")), "receipts.json");
  const issuer = new ToolInvocationReceiptJournal(SECRET, { clock: time.now, statePath });
  const capability = issuer.issueCapability(receiptRequest());
  const first = new ToolInvocationReceiptJournal(SECRET, { clock: time.now, statePath });
  const second = new ToolInvocationReceiptJournal(SECRET, { clock: time.now, statePath });
  const pre = first.consumeCapability({
    ...receiptRequest(), capability, host_tool_execution_id: "hte_instance-one"
  });
  assert.equal(pre.host_tool_execution_id, "hte_instance-one");
  assert.throws(
    () => second.consumeCapability({
      ...receiptRequest(), capability, host_tool_execution_id: "hte_instance-two"
    }),
    /replay/i
  );
  const restarted = new ToolInvocationReceiptJournal(SECRET, { clock: time.now, statePath });
  assert.throws(
    () => restarted.consumeCapability({
      ...receiptRequest(), capability, host_tool_execution_id: "hte_after-restart"
    }),
    /replay/i
  );
});

test("receipt binding rejects each Bridge-private context and request digest drift", () => {
  const journal = new ToolInvocationReceiptJournal(SECRET);
  const capability = journal.issueCapability(receiptRequest());
  for (const [field, value] of [
    ["bridge_challenge_nonce", "different-nonce"],
    ["workspace", "/tmp/not-the-canonical-workspace"],
    ["production_release_revision", "b".repeat(40)],
    ["production_endpoint_generation", 8],
    ["bridge_principal_id", "different-principal"],
    ["bridge_execution_session_id", "different-session"],
    ["bridge_capability_id", "different-capability"],
    ["bridge_workspace_id", "different-workspace"],
    ["outer_request_sha256", "1".repeat(64)],
    ["forwarded_request_sha256", "2".repeat(64)],
    ["normalized_request_sha256", "3".repeat(64)]
  ]) {
    assert.throws(
      () => journal.consumeCapability({
        ...receiptRequest({ [field]: value }), capability, host_tool_execution_id: "hte_" + field
      }),
      /mismatch/i,
      field
    );
  }
});
