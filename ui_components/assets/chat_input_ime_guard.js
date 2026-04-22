(() => {
  if (window.__codexNomadChatInputImeGuardInstalled) {
    return;
  }
  window.__codexNomadChatInputImeGuardInstalled = true;

  const ENTER_KEYS = new Set(["Enter", "NumpadEnter"]);
  const RECENT_COMPOSITION_WINDOW_MS = 200;

  const isChatInputTarget = (target) =>
    target instanceof HTMLElement &&
    target.dataset?.testid === "stChatInputTextArea";

  document.addEventListener(
    "compositionstart",
    (event) => {
      if (!isChatInputTarget(event.target)) {
        return;
      }
      event.target.dataset.codexImeComposing = "true";
      event.target.dataset.codexImeLastCompositionAt = String(Date.now());
    },
    true,
  );

  document.addEventListener(
    "compositionend",
    (event) => {
      if (!isChatInputTarget(event.target)) {
        return;
      }
      event.target.dataset.codexImeComposing = "false";
      event.target.dataset.codexImeLastCompositionAt = String(Date.now());
    },
    true,
  );

  document.addEventListener(
    "keydown",
    (event) => {
      if (!isChatInputTarget(event.target)) {
        return;
      }
      if (!ENTER_KEYS.has(event.key) && event.keyCode !== 13 && event.keyCode !== 229) {
        return;
      }
      if (event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) {
        return;
      }

      const lastCompositionAt = Number(
        event.target.dataset.codexImeLastCompositionAt || "0",
      );
      const recentlyComposed = Date.now() - lastCompositionAt < RECENT_COMPOSITION_WINDOW_MS;
      const composing =
        event.isComposing ||
        event.keyCode === 229 ||
        event.target.dataset.codexImeComposing === "true" ||
        recentlyComposed;

      if (!composing) {
        return;
      }

      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
    },
    true,
  );
})();
