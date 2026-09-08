export async function pageSubmitFunction(payloadValue, expectedConversationValue) {
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

  async function waitForSubmitConfirmation(composer, expectedConversation, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() <= deadline) {
      let routeMatches = false;
      try {
        routeMatches = conversationFromCurrentLocation() === expectedConversation;
      } catch {
        routeMatches = false;
      }
      if (routeMatches && normalizedComposerText(composer) === "") return true;
      await sleep(25);
    }
    return false;
  }

  const payload = String(payloadValue == null ? "" : payloadValue);
  const expectedConversation = String(expectedConversationValue == null ? "" : expectedConversationValue).trim();

  if (!payload.trim() || payload.length > 8000) {
    return {
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
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: false,
      status: "wrong_conversation"
    };
  }
  if (currentConversation !== expectedConversation) {
    return {
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: false,
      status: "wrong_conversation"
    };
  }

  const composer = document.querySelector('#prompt-textarea[contenteditable="true"]');
  if (!composer) {
    return {
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: true,
      status: "composer_unavailable"
    };
  }
  if (normalizedComposerText(composer) !== "") {
    return {
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: false,
      status: "composer_not_empty"
    };
  }

  insertPayload(composer, payload);

  const sendButton = await waitForSendButton(1500);
  if (!sendButton) {
    const cleaned = clearOwnPayloadIfSafe(composer, payload);
    return {
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: cleaned,
      status: cleaned ? "send_unavailable" : "composer_changed_before_dispatch"
    };
  }

  if (conversationFromCurrentLocation() !== expectedConversation) {
    clearOwnPayloadIfSafe(composer, payload);
    return {
      dispatch_attempted: false,
      submit_confirmed: false,
      retryable: false,
      status: "wrong_conversation_before_dispatch"
    };
  }

  try {
    sendButton.click();
  } catch {
    return {
      dispatch_attempted: true,
      submit_confirmed: false,
      retryable: false,
      status: "result_unknown"
    };
  }

  try {
    const confirmed = await waitForSubmitConfirmation(composer, expectedConversation, 2500);
    return {
      dispatch_attempted: true,
      submit_confirmed: Boolean(confirmed),
      retryable: false,
      status: confirmed ? "submit_confirmed" : "result_unknown"
    };
  } catch {
    return {
      dispatch_attempted: true,
      submit_confirmed: false,
      retryable: false,
      status: "result_unknown"
    };
  }
}
