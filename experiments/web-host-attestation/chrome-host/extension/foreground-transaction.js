async function activeTab(chromeApi) {
  const tabs = await chromeApi.tabs.query({ active: true, lastFocusedWindow: true });
  const tab = Array.isArray(tabs) ? tabs[0] : null;
  return tab && Number.isInteger(tab.id) && Number.isInteger(tab.windowId)
    ? { tab_id: tab.id, window_id: tab.windowId }
    : null;
}

export async function withConditionalForegroundTransaction(options) {
  const chromeApi = options && options.chromeApi;
  const targetTabId = Number(options && options.targetTabId);
  const run = options && options.run;
  if (!chromeApi || typeof chromeApi.tabs?.query !== "function" ||
      typeof chromeApi.tabs?.update !== "function" ||
      typeof chromeApi.windows?.update !== "function") {
    throw new Error("tabs/windows APIs are required for foreground transaction");
  }
  if (!Number.isInteger(targetTabId)) throw new Error("targetTabId is required");
  if (typeof run !== "function") throw new Error("foreground transaction callback is required");

  const before = await activeTab(chromeApi);
  const targetTab = await chromeApi.tabs.get(targetTabId);
  if (!targetTab || !Number.isInteger(targetTab.windowId)) {
    throw new Error("target tab has no window");
  }

  const alreadyActive = before && before.tab_id === targetTabId;
  let activatedByHost = false;
  if (!alreadyActive) {
    await chromeApi.tabs.update(targetTabId, { active: true });
    await chromeApi.windows.update(targetTab.windowId, { focused: true });
    activatedByHost = true;
  }

  let result;
  let failure;
  try {
    result = await run();
  } catch (error) {
    failure = error;
  }

  let restored = false;
  let userChangedFocus = false;
  if (activatedByHost && before) {
    const after = await activeTab(chromeApi);
    if (after && after.tab_id === targetTabId && after.window_id === targetTab.windowId) {
      await chromeApi.tabs.update(before.tab_id, { active: true });
      await chromeApi.windows.update(before.window_id, { focused: true });
      restored = true;
    } else {
      userChangedFocus = true;
    }
  }

  if (failure) throw failure;
  return Object.freeze({
    result,
    focus_transaction: Object.freeze({
      mode: alreadyActive ? "already_active" : "foreground_transaction",
      activated_by_host: activatedByHost,
      previous_tab_id: before && before.tab_id,
      previous_window_id: before && before.window_id,
      restored_previous_active: restored,
      user_changed_focus: userChangedFocus
    })
  });
}
