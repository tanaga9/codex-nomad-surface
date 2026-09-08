(() => {
  if (window.__codexNomadChatInputDragGuardInstalled) return;
  window.__codexNomadChatInputDragGuardInstalled = true;

  const marker = "data-codex-inactive-chat-dropzone";
  const style = document.createElement("style");
  style.textContent = `[${marker}] { display: none !important; }`;
  document.head.append(style);
  let active = false;
  let generation = 0;
  const marked = new Set();

  const restore = () => {
    for (const element of marked) element.removeAttribute(marker);
    marked.clear();
  };
  const coversParent = (element) => {
    const css = window.getComputedStyle(element);
    return css.position === "absolute" &&
      [css.top, css.right, css.bottom, css.left].every(value => value === "0px");
  };
  const reconcile = () => {
    restore();
    if (active) return;
    // Fail closed if Streamlit changes the overlay structure. Only recognize
    // the full-size input wrapper + non-interactive label pair inside chat.
    // Never alter React state, remove nodes, or dispatch synthetic events.
    for (const root of document.querySelectorAll('[data-testid="stChatInput"]')) {
      for (const input of root.querySelectorAll('input[type="file"]')) {
        const dropzone = input.parentElement;
        const label = dropzone?.nextElementSibling;
        if (!label || dropzone.children.length !== 1 ||
            label.children.length !== 0 || !label.textContent.trim() ||
            !coversParent(dropzone) || !coversParent(label) ||
            window.getComputedStyle(label).pointerEvents !== "none") continue;
        for (const element of [dropzone, label]) {
          element.setAttribute(marker, "");
          marked.add(element);
        }
      }
    }
  };
  const finish = () => {
    active = false;
    generation += 1;
    reconcile();
  };
  window.addEventListener("dragover", (event) => {
    if (!Array.from(event.dataTransfer?.types || []).includes("Files")) return;
    active = true;
    generation += 1;
    restore();
  }, true);
  window.addEventListener("dragleave", (event) => {
    if (event.clientX <= 0 || event.clientY <= 0 ||
        event.clientX >= window.innerWidth || event.clientY >= window.innerHeight) finish();
  }, true);
  window.addEventListener("drop", () => {
    const droppedGeneration = generation;
    // Preserve the native drop target until event dispatch has completed.
    setTimeout(() => {
      if (generation === droppedGeneration) finish();
    }, 0);
  }, true);
  window.addEventListener("dragend", finish, true);
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") finish();
  }, true);
  window.addEventListener("mousemove", (event) => {
    if (active && event.buttons === 0) finish();
  }, true);
  window.addEventListener("blur", (event) => {
    if (event.target === window) finish();
  }, true);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) finish();
  });
  // React can mount its overlay after the terminating event, or remount chat
  // during a rerun. Observe structure only; our attributes cannot loop back.
  const chatSelector = '[data-testid="stChatInput"]';
  new MutationObserver((records) => {
    const affectsChat = records.some(record =>
      record.target.closest?.(chatSelector) ||
      [...record.addedNodes, ...record.removedNodes].some(node =>
        node.matches?.(chatSelector) || node.querySelector?.(chatSelector)));
    if (affectsChat) reconcile();
  }).observe(document.body, { childList: true, subtree: true });
  reconcile();
})();
