export async function pageSubmitOperationFunction(operationValue, inputValue) {
  function normalizedText(value) {
    return String(value == null ? "" : value)
      .split(String.fromCharCode(13) + String.fromCharCode(10))
      .join(String.fromCharCode(10))
      .trim();
  }

  function normalizedComposerText(element) {
    if (!element) return "";
    const raw = typeof element.innerText === "string"
      ? element.innerText
      : String(element.textContent || "");
    return normalizedText(raw);
  }

  function conversationFromCurrentLocation() {
    const url = new URL(globalThis.location.href);
    if (url.protocol !== "https:" || url.hostname !== "chatgpt.com") {
      throw new Error("page is not secure chatgpt.com");
    }
    const parts = url.pathname.split("/").filter(Boolean);
    const index = parts.lastIndexOf("c");
    const conversation = index >= 0 ? parts[index + 1] : "";
    if (!/^[0-9a-f-]{20,80}$/i.test(conversation)) {
      throw new Error("page has no stable ChatGPT conversation route");
    }
    return conversation;
  }

  function dispatchInput(element, inputType) {
    const EventType = globalThis.InputEvent || globalThis.Event;
    if (typeof EventType === "function") {
      try {
        element.dispatchEvent(new EventType("input", {
          bubbles: true,
          inputType: inputType || "insertText"
        }));
        return;
      } catch {
      }
    }
    if (typeof element.dispatchEvent === "function") {
      element.dispatchEvent({ type: "input", bubbles: true });
    }
  }

  function insertPayload(composer, payload) {
    composer.focus();
    let inserted = false;
    if (typeof document.execCommand === "function") {
      try {
        inserted = document.execCommand("insertText", false, payload) === true;
      } catch {
        inserted = false;
      }
    }
    if (!inserted) {
      composer.textContent = payload;
      if ("innerText" in composer) composer.innerText = payload;
    }
    dispatchInput(composer, "insertText");
  }

  function currentUserMessageCount() {
    return document.querySelectorAll('[data-message-author-role="user"]').length;
  }

  function responseIsActive() {
    const stop = document.querySelector('button[data-testid="stop-button"]');
    if (stop) return true;
    const labelled = Array.from(document.querySelectorAll("button,[role=button]")).find(function (node) {
      const label = String(node.getAttribute && node.getAttribute("aria-label") || "")
        .trim()
        .toLowerCase();
      return label === "stop answering";
    });
    return Boolean(labelled);
  }

  function resolveSendControl() {
    const candidates = [
      {
        kind: "testid_send_button",
        node: document.querySelector('button[data-testid="send-button"]')
      },
      {
        kind: "aria_send",
        node: Array.from(document.querySelectorAll("button")).find(function (node) {
          const label = String(node.getAttribute && node.getAttribute("aria-label") || "")
            .trim()
            .toLowerCase();
          return label === "send" || label === "send message";
        }) || null
      }
    ];
    for (const candidate of candidates) {
      const node = candidate.node;
      if (!node) continue;
      if (node.disabled === true) continue;
      if (String(node.getAttribute && node.getAttribute("aria-disabled") || "") === "true") continue;
      let rect = null;
      try {
        rect = node.getBoundingClientRect();
      } catch {
        rect = null;
      }
      const width = Number(rect && rect.width);
      const height = Number(rect && rect.height);
      const x = Number(rect && (rect.left + width / 2));
      const y = Number(rect && (rect.top + height / 2));
      if (!rect || !Number.isFinite(x) || !Number.isFinite(y) ||
          !Number.isFinite(width) || !Number.isFinite(height) ||
          width <= 0 || height <= 0) {
        continue;
      }
      return {
        resolved: true,
        selector_kind: candidate.kind,
        x,
        y,
        width,
        height
      };
    }
    return {
      resolved: false,
      selector_kind: null,
      x: null,
      y: null,
      width: null,
      height: null
    };
  }

  async function sha256Hex(value) {
    const data = new TextEncoder().encode(String(value));
    const digest = await globalThis.crypto.subtle.digest("SHA-256", data);
    return Array.from(new Uint8Array(digest))
      .map(function (byte) { return byte.toString(16).padStart(2, "0"); })
      .join("");
  }

  const operation = String(operationValue == null ? "" : operationValue).trim();
  const input = inputValue && typeof inputValue === "object" ? inputValue : {};
  const expectedConversation = String(input.expected_conversation_id == null
    ? ""
    : input.expected_conversation_id).trim();

  if (operation === "prepare") {
    const payload = String(input.payload == null ? "" : input.payload);
    if (!payload.trim() || payload.length > 8000) {
      return {
        prepared: false,
        ready_to_dispatch: false,
        dispatch_attempted: false,
        submit_confirmed: false,
        retryable: false,
        status: "invalid_payload",
        baseline_user_message_count: null,
        initial_send_control: null
      };
    }
    let currentConversation;
    try {
      currentConversation = conversationFromCurrentLocation();
    } catch {
      return {
        prepared: false,
        ready_to_dispatch: false,
        dispatch_attempted: false,
        submit_confirmed: false,
        retryable: false,
        status: "wrong_conversation",
        baseline_user_message_count: null,
        initial_send_control: null
      };
    }
    if (currentConversation !== expectedConversation) {
      return {
        prepared: false,
        ready_to_dispatch: false,
        dispatch_attempted: false,
        submit_confirmed: false,
        retryable: false,
        status: "wrong_conversation",
        baseline_user_message_count: null,
        initial_send_control: null
      };
    }

    const composer = document.querySelector('#prompt-textarea[contenteditable="true"]');
    if (!composer) {
      return {
        prepared: false,
        ready_to_dispatch: false,
        dispatch_attempted: false,
        submit_confirmed: false,
        retryable: true,
        status: "composer_unavailable",
        baseline_user_message_count: null,
        initial_send_control: null
      };
    }
    const existingComposerText = normalizedComposerText(composer);
    const ownsPreparedPayload = existingComposerText === payload.trim();
    if (existingComposerText !== "" && !ownsPreparedPayload) {
      return {
        prepared: false,
        ready_to_dispatch: false,
        dispatch_attempted: false,
        submit_confirmed: false,
        retryable: false,
        status: "composer_not_empty",
        baseline_user_message_count: currentUserMessageCount(),
        initial_send_control: null
      };
    }
    if (responseIsActive()) {
      return {
        prepared: false,
        ready_to_dispatch: false,
        dispatch_attempted: false,
        submit_confirmed: false,
        retryable: true,
        status: "controller_active",
        baseline_user_message_count: currentUserMessageCount(),
        initial_send_control: null
      };
    }

    const baseline = currentUserMessageCount();
    if (!ownsPreparedPayload) {
      insertPayload(composer, payload);
    }
    if (normalizedComposerText(composer) !== payload.trim()) {
      return {
        prepared: false,
        ready_to_dispatch: false,
        dispatch_attempted: false,
        submit_confirmed: false,
        retryable: false,
        status: "composer_preparation_unconfirmed",
        baseline_user_message_count: baseline,
        initial_send_control: null
      };
    }
    const initial = resolveSendControl();
    return {
      prepared: true,
      ready_to_dispatch: initial.resolved,
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: !initial.resolved,
      status: initial.resolved ? "prepared" : "prepared_send_unavailable",
      baseline_user_message_count: baseline,
      initial_send_control: initial
    };
  }

  if (operation === "resolve_send") {
    const payload = String(input.payload == null ? "" : input.payload);
    try {
      if (conversationFromCurrentLocation() !== expectedConversation) {
        return {
          resolved: false,
          status: "wrong_conversation",
          composer_exact_payload: false,
          send_control: null
        };
      }
    } catch {
      return {
        resolved: false,
        status: "wrong_conversation",
        composer_exact_payload: false,
        send_control: null
      };
    }
    const composer = document.querySelector('#prompt-textarea[contenteditable="true"]');
    if (!composer) {
      return {
        resolved: false,
        status: "composer_unavailable",
        composer_exact_payload: false,
        send_control: null
      };
    }
    if (normalizedComposerText(composer) !== payload.trim()) {
      return {
        resolved: false,
        status: "composer_changed_before_dispatch",
        composer_exact_payload: false,
        send_control: null
      };
    }
    if (responseIsActive()) {
      return {
        resolved: false,
        status: "controller_active",
        composer_exact_payload: true,
        send_control: null
      };
    }
    const control = resolveSendControl();
    return {
      resolved: control.resolved,
      status: control.resolved ? "send_control_resolved" : "send_unavailable",
      composer_exact_payload: true,
      send_control: control
    };
  }

  if (operation === "confirm") {
    const payload = String(input.payload == null ? "" : input.payload);
    const baseline = Number(input.baseline_user_message_count);
    const baselineCount = Number.isSafeInteger(baseline) && baseline >= 0 ? baseline : 0;
    const timeoutMs = Number(input.timeout_ms == null ? 4000 : input.timeout_ms);
    const boundedTimeout = Number.isFinite(timeoutMs) && timeoutMs >= 250 && timeoutMs <= 10000
      ? Math.floor(timeoutMs)
      : 4000;
    const deadline = Date.now() + boundedTimeout;

    function exactNewPayloadMessageExists() {
      const nodes = Array.from(document.querySelectorAll('[data-message-author-role="user"]'));
      for (let index = baselineCount; index < nodes.length; index += 1) {
        const node = nodes[index];
        const raw = typeof node.innerText === "string"
          ? node.innerText
          : String(node.textContent || "");
        if (normalizedText(raw) === payload.trim()) return true;
      }
      return false;
    }

    while (Date.now() <= deadline) {
      let routeMatches = false;
      try {
        routeMatches = conversationFromCurrentLocation() === expectedConversation;
      } catch {
        routeMatches = false;
      }
      if (routeMatches) {
        const composer = document.querySelector('#prompt-textarea[contenteditable="true"]');
        const composerEmpty = normalizedComposerText(composer) === "";
        const messageExists = exactNewPayloadMessageExists();
        if (messageExists) {
          return {
            dispatch_attempted: true,
            submit_confirmed: true,
            retryable: false,
            status: "submit_confirmed",
            composer_empty: composerEmpty,
            exact_new_user_message: true
          };
        }
      }
      await new Promise(function (resolve) { setTimeout(resolve, 25); });
    }

    const composer = document.querySelector('#prompt-textarea[contenteditable="true"]');
    return {
      dispatch_attempted: true,
      submit_confirmed: false,
      retryable: false,
      status: "result_unknown",
      composer_empty: normalizedComposerText(composer) === "",
      composer_still_payload: normalizedComposerText(composer) === payload.trim(),
      exact_new_user_message: false
    };
  }

  if (operation === "prepare_rollback") {
    const expectedHash = String(input.expected_payload_sha256 == null
      ? ""
      : input.expected_payload_sha256).trim().toLowerCase();
    if (!/^[0-9a-f]{64}$/.test(expectedHash)) {
      return { safe_to_clear: false, status: "invalid_payload_hash", composer_empty: false };
    }
    try {
      if (conversationFromCurrentLocation() !== expectedConversation) {
        return { safe_to_clear: false, status: "wrong_conversation", composer_empty: false };
      }
    } catch {
      return { safe_to_clear: false, status: "wrong_conversation", composer_empty: false };
    }
    const composer = document.querySelector('#prompt-textarea[contenteditable="true"]');
    if (!composer) {
      return { safe_to_clear: false, status: "composer_unavailable", composer_empty: false };
    }
    const text = normalizedComposerText(composer);
    if (text === "") {
      return { safe_to_clear: false, status: "already_absent", composer_empty: true };
    }
    const actualHash = await sha256Hex(text);
    if (actualHash !== expectedHash) {
      return { safe_to_clear: false, status: "composer_hash_mismatch", composer_empty: false };
    }
    composer.focus();
    return { safe_to_clear: true, status: "exact_owned_payload", composer_empty: false };
  }

  if (operation === "verify_empty") {
    try {
      if (conversationFromCurrentLocation() !== expectedConversation) {
        return { composer_empty: false, status: "wrong_conversation" };
      }
    } catch {
      return { composer_empty: false, status: "wrong_conversation" };
    }
    const composer = document.querySelector('#prompt-textarea[contenteditable="true"]');
    return {
      composer_empty: normalizedComposerText(composer) === "",
      status: composer ? "observed" : "composer_unavailable"
    };
  }

  if (operation === "reconcile") {
    const expectedPayloadSha256 = String(input.expected_payload_sha256 == null
      ? ""
      : input.expected_payload_sha256).trim().toLowerCase();
    if (!/^[0-9a-f]{64}$/.test(expectedPayloadSha256)) {
      return {
        status: "invalid_payload_hash",
        conversation_matches: null,
        composer_exact_payload: null,
        composer_empty: null,
        exact_user_message_present: null
      };
    }
    let currentConversation;
    try {
      currentConversation = conversationFromCurrentLocation();
    } catch {
      return {
        status: "wrong_conversation",
        conversation_matches: null,
        composer_exact_payload: null,
        composer_empty: null,
        exact_user_message_present: null
      };
    }
    if (currentConversation !== expectedConversation) {
      return {
        status: "wrong_conversation",
        conversation_matches: false,
        composer_exact_payload: null,
        composer_empty: null,
        exact_user_message_present: null
      };
    }

    const baselineUserMessageCount = Number(input.baseline_user_message_count);
    if (!Number.isSafeInteger(baselineUserMessageCount) || baselineUserMessageCount < 0) {
      return {
        status: "invalid_baseline_user_message_count",
        conversation_matches: true,
        composer_exact_payload: null,
        composer_empty: null,
        exact_user_message_present: null
      };
    }

    async function exactPayloadMessageExists() {
      const nodes = Array.from(document.querySelectorAll('[data-message-author-role="user"]'))
        .slice(baselineUserMessageCount);
      for (let index = 0; index < nodes.length; index += 1) {
        const node = nodes[index];
        const raw = typeof node.innerText === "string"
          ? node.innerText
          : String(node.textContent || "");
        if (await sha256Hex(normalizedText(raw)) === expectedPayloadSha256) return true;
      }
      return false;
    }

    const exactUserMessage = await exactPayloadMessageExists();
    const composer = document.querySelector('#prompt-textarea[contenteditable="true"]');
    if (!composer) {
      return {
        status: "composer_unavailable",
        conversation_matches: true,
        composer_exact_payload: null,
        composer_empty: null,
        exact_user_message_present: exactUserMessage
      };
    }
    const composerText = normalizedComposerText(composer);
    const composerExact = composerText !== "" &&
      await sha256Hex(composerText) === expectedPayloadSha256;
    return {
      status: "observed",
      conversation_matches: true,
      composer_exact_payload: composerExact,
      composer_empty: composerText === "",
      exact_user_message_present: exactUserMessage
    };
  }

  return { status: "unsupported_operation" };
}
