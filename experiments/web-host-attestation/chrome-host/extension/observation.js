export const MACHINE_SOURCE = "browser_cdp_page_frame_tree_v1";
const CONVERSATION_RE = /^[0-9a-f-]{20,80}$/i;

function requiredText(value, name, max) {
  const text = String(value == null ? "" : value).trim();
  const limit = max == null ? 2048 : max;
  if (!text) throw new Error(name + " is required");
  if (text.length > limit) throw new Error(name + " exceeds limit");
  return text;
}

export function conversationIdFromChatgptUrl(urlValue) {
  const url = new URL(requiredText(urlValue, "target url"));
  if (url.protocol !== "https:" || url.hostname !== "chatgpt.com") {
    throw new Error("target is not secure chatgpt.com");
  }
  const parts = url.pathname.split("/").filter(Boolean);
  const index = parts.lastIndexOf("c");
  const conversation = index >= 0 ? parts[index + 1] : null;
  if (!conversation || !CONVERSATION_RE.test(conversation)) {
    throw new Error("target has no stable ChatGPT conversation route");
  }
  return conversation;
}

export function observationFromDebugger(input) {
  const target = input && input.targetInfo;
  const frameTree = input && input.frameTree;
  const observedAtMs = input && input.observedAtMs;
  const targetId = requiredText(target && target.id, "browser target id", 256);
  if (!target || target.type !== "page") throw new Error("browser target must be a page");
  const targetConversation = conversationIdFromChatgptUrl(target.url);

  const top = frameTree && (frameTree.frameTree ? frameTree.frameTree.frame : (frameTree.frame ? frameTree.frame : frameTree));
  if (!top || typeof top !== "object") throw new Error("top frame machine record is required");
  const origin = requiredText(top.securityOrigin, "top frame securityOrigin", 256);
  if (origin !== "https://chatgpt.com") throw new Error("top frame is not secure chatgpt.com");
  const frameConversation = conversationIdFromChatgptUrl(top.url);
  if (frameConversation !== targetConversation) {
    throw new Error("debugger target route and top frame conversation differ");
  }
  const observed = Number(observedAtMs);
  if (!Number.isSafeInteger(observed) || observed < 1) throw new Error("observed_at_ms is invalid");

  return Object.freeze({
    host_type: "chatgpt_web",
    browser_target_id: targetId,
    top_frame_id: requiredText(top.id, "top frame id", 256),
    loader_id: requiredText(top.loaderId, "loader id", 256),
    secure_origin: origin,
    conversation_id: frameConversation,
    observed_at_ms: observed,
    source: MACHINE_SOURCE
  });
}

export function selectUniqueConversationTarget(targets, conversationId) {
  const expected = requiredText(conversationId, "conversation_id", 128);
  const matches = (targets || []).filter(function (target) {
    try {
      return target && target.type === "page" &&
        conversationIdFromChatgptUrl(target.url) === expected;
    } catch {
      return false;
    }
  });
  if (matches.length === 0) throw new Error("exact ChatGPT conversation target is unavailable");
  if (matches.length !== 1) throw new Error("exact ChatGPT conversation target is ambiguous");
  return matches[0];
}

export function selectExactTarget(targets, browserTargetId) {
  const expected = requiredText(browserTargetId, "browser_target_id", 256);
  const matches = (targets || []).filter(function (target) {
    return target && target.type === "page" && target.id === expected;
  });
  if (matches.length !== 1) throw new Error("exact browser target is unavailable or replaced");
  conversationIdFromChatgptUrl(matches[0].url);
  return matches[0];
}
