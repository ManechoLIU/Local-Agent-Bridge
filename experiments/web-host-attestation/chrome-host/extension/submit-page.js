export async function pagePrepareSubmitFunction(payloadValue, expectedConversationValue) {
  function normalizedComposerText(element) {
    const raw = typeof element.innerText === "string"
      ? element.innerText
      : String(element.textContent || "");
    return raw.replace(/\r\n/g, "\n").trim();
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

  async function sleep(ms) {
    await new Promise(function (resolve) {
      setTimeout(resolve, ms);
    });
  }

  function dispatchInput(element) {
    const EventType = globalThis.InputEvent || globalThis.Event;
    if (typeof EventType === "function") {
      try {
        element.dispatchEvent(new EventType("input", {
          bubbles: true,
          inputType: "insertText"
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
    dispatchInput(composer);
  }

  function clearOwnPayloadIfSafe(composer, payload) {
    if (normalizedComposerText(composer) !== payload.trim()) return false;
    composer.textContent = "";
    if ("innerText" in composer) composer.innerText = "";
    dispatchInput(composer);
    return true;
  }

  async function waitForSendButton(timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() <= deadline) {
      const button = document.querySelector('button[data-testid="send-button"]');
      if (button &&
          button.disabled !== true &&
          String(button.getAttribute && button.getAttribute("aria-disabled") || "") !== "true") {
        return button;
      }
      await sleep(25);
    }
    return null;
  }

  const payload = String(payloadValue == null ? "" : payloadValue);
  const expectedConversation = String(expectedConversationValue == null ? "" : expectedConversationValue).trim();

  if (!payload.trim() || payload.length > 8000) {
    return {
      ready_to_dispatch: false,
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: false,
      status: "invalid_payload"
    };
  }

  let currentConversation;
  try {
    currentConversation = conversationFromCurrentLocation();
  } catch {
    return {
      ready_to_dispatch: false,
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: false,
      status: "wrong_conversation"
    };
  }
  if (currentConversation !== expectedConversation) {
    return {
      ready_to_dispatch: false,
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: false,
      status: "wrong_conversation"
    };
  }

  const composer = document.querySelector('#prompt-textarea[contenteditable="true"]');
  if (!composer) {
    return {
      ready_to_dispatch: false,
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: true,
      status: "composer_unavailable"
    };
  }

  const existingComposerText = normalizedComposerText(composer);
  const ownsPreparedPayload = existingComposerText === payload.trim();
  if (existingComposerText !== "" && !ownsPreparedPayload) {
    return {
      ready_to_dispatch: false,
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: false,
      status: "composer_not_empty"
    };
  }

  if (!ownsPreparedPayload) {
    insertPayload(composer, payload);
  }

  const sendButton = await waitForSendButton(1500);
  if (!sendButton) {
    return {
      ready_to_dispatch: false,
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: normalizedComposerText(composer) === payload.trim(),
      status: normalizedComposerText(composer) === payload.trim()
        ? "send_unavailable"
        : "composer_changed_before_dispatch"
    };
  }

  if (conversationFromCurrentLocation() !== expectedConversation) {
    clearOwnPayloadIfSafe(composer, payload);
    return {
      ready_to_dispatch: false,
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: false,
      status: "wrong_conversation_before_dispatch"
    };
  }

  let rect;
  try {
    rect = sendButton.getBoundingClientRect();
  } catch {
    rect = null;
  }
  const x = Number(rect && (rect.left + rect.width / 2));
  const y = Number(rect && (rect.top + rect.height / 2));
  if (!rect || !Number.isFinite(x) || !Number.isFinite(y) ||
      Number(rect.width) <= 0 || Number(rect.height) <= 0) {
    const cleaned = clearOwnPayloadIfSafe(composer, payload);
    return {
      ready_to_dispatch: false,
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: cleaned,
      status: cleaned ? "send_button_not_clickable" : "composer_changed_before_dispatch"
    };
  }

  return {
    ready_to_dispatch: true,
    dispatch_attempted: false,
    submit_confirmed: false,
    retryable: false,
    status: "ready_to_dispatch",
    click_point: { x: x, y: y }
  };
}

export async function pageConfirmSubmitFunction(payloadValue, expectedConversationValue, timeoutMsValue) {
  function normalizedText(value) {
    return String(value == null ? "" : value).replace(/\r\n/g, "\n").trim();
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

  async function sleep(ms) {
    await new Promise(function (resolve) {
      setTimeout(resolve, ms);
    });
  }

  function exactPayloadUserMessageExists(payload) {
    const nodes = document.querySelectorAll('[data-message-author-role="user"]');
    for (const node of nodes) {
      const raw = typeof node.innerText === "string"
        ? node.innerText
        : String(node.textContent || "");
      if (normalizedText(raw) === payload.trim()) return true;
    }
    return false;
  }

  const payload = String(payloadValue == null ? "" : payloadValue);
  const expectedConversation = String(expectedConversationValue == null ? "" : expectedConversationValue).trim();
  const timeoutMs = Number(timeoutMsValue == null ? 4000 : timeoutMsValue);
  const boundedTimeout = Number.isFinite(timeoutMs) && timeoutMs >= 250 && timeoutMs <= 10000
    ? Math.floor(timeoutMs)
    : 4000;
  const deadline = Date.now() + boundedTimeout;

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
      const messageExists = exactPayloadUserMessageExists(payload);
      if (composerEmpty || messageExists) {
        return {
          dispatch_attempted: true,
          submit_confirmed: true,
          retryable: false,
          status: "submit_confirmed"
        };
      }
    }
    await sleep(25);
  }

  const composer = document.querySelector('#prompt-textarea[contenteditable="true"]');
  return {
    dispatch_attempted: true,
    submit_confirmed: false,
    retryable: false,
    status: "result_unknown",
    composer_still_payload: normalizedComposerText(composer) === payload.trim(),
    exact_user_message_present: exactPayloadUserMessageExists(payload)
  };
}
