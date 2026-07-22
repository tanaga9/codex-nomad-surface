(() => {
  if (window.__codexNomadChatInputOutboxInstalled) return;
  window.__codexNomadChatInputOutboxInstalled = true;

  const KEY_PREFIX = "codexNomadSurface.chatOutbox.v1.";
  const RECOVERY_DELAY_MS = 5000;
  let scope = "";
  let recoveryTimer = null;
  const minimizedScopes = new Set();
  const storageKey = (kind) => `${KEY_PREFIX}${kind}.${encodeURIComponent(scope || "default")}`;
  const chatInput = () => document.querySelector('[data-testid="stChatInputTextArea"]');
  const readPending = () => {
    try {
      const raw = sessionStorage.getItem(storageKey("pending"));
      const value = raw ? JSON.parse(raw) : null;
      return value && typeof value.text === "string" && value.text ? value : null;
    } catch (_) {
      return null;
    }
  };
  const writePending = (text) => {
    if (!text) return;
    minimizedScopes.delete(scope);
    try {
      sessionStorage.setItem(storageKey("pending"), JSON.stringify({ text, createdAt: Date.now() }));
    } catch (_) {}
    refreshRecovery();
  };
  const clearPending = (expectedText = "", targetScope = scope) => {
    const previousScope = scope;
    scope = targetScope || "";
    const pending = readPending();
    if (expectedText && pending?.text !== expectedText) {
      scope = previousScope;
      return;
    }
    try { sessionStorage.removeItem(storageKey("pending")); } catch (_) {}
    scope = previousScope;
    refreshRecovery();
  };
  const copy = async (text) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch (_) {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.cssText = "position:fixed;opacity:0";
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
  };
  const recoveryRoot = () => {
    let root = document.getElementById("codex-nomad-outbox-recovery");
    if (root) return root;
    root = document.createElement("div");
    root.id = "codex-nomad-outbox-recovery";
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-label", "Unconfirmed message recovery");
    root.style.cssText = [
      "position:fixed", "left:50%", "bottom:5.2rem", "z-index:9999",
      "transform:translateX(-50%)", "width:min(30rem,calc(100vw - 1.6rem))",
      "padding:1rem 1.15rem", "border-radius:.6rem",
      "box-shadow:0 0.45rem 1.4rem rgba(0,0,0,.3)", "font:inherit",
    ].join(";");
    document.body.appendChild(root);
    return root;
  };
  const themeValue = (names, fallback) => {
    const nodes = [
      document.querySelector('[data-testid="stAppViewContainer"]'),
      document.querySelector(".stApp"), document.body, document.documentElement,
    ].filter(Boolean);
    for (const node of nodes) {
      const style = getComputedStyle(node);
      for (const name of names) {
        const value = style.getPropertyValue(name).trim();
        if (value) return value;
      }
    }
    return fallback;
  };
  const isTransparent = (color) => !color || color === "transparent" || /^rgba\([^)]*,\s*0\)$/.test(color);
  const opaqueAppBackground = () => {
    const nodes = [
      document.querySelector('[data-testid="stAppViewContainer"]'),
      document.querySelector(".stApp"), document.querySelector("main"),
      document.body, document.documentElement,
    ].filter(Boolean);
    for (const node of nodes) {
      const color = getComputedStyle(node).backgroundColor;
      if (!isTransparent(color)) return color;
    }
    return matchMedia("(prefers-color-scheme: dark)").matches ? "#0e1117" : "#ffffff";
  };
  const applyTheme = (root) => {
    const app = document.querySelector('[data-testid="stAppViewContainer"]') || document.body;
    const style = getComputedStyle(app);
    root.style.backgroundColor = themeValue(
      ["--st-secondary-background-color", "--secondary-background-color", "--background-color"],
      isTransparent(style.backgroundColor) ? opaqueAppBackground() : style.backgroundColor,
    );
    root.style.color = themeValue(["--st-text-color", "--text-color"], style.color || "CanvasText");
    root.style.borderColor = themeValue(["--st-warning-color", "--warning-color"], "currentColor");
  };
  const refreshRecovery = () => {
    const pending = readPending();
    const root = recoveryRoot();
    if (recoveryTimer !== null) {
      clearTimeout(recoveryTimer);
      recoveryTimer = null;
    }
    applyTheme(root);
    root.replaceChildren();
    root.style.display = "none";
    if (!pending) return;
    const createdAt = Number(pending.createdAt) || Date.now();
    const remainingDelay = RECOVERY_DELAY_MS - (Date.now() - createdAt);
    if (remainingDelay > 0) {
      recoveryTimer = setTimeout(refreshRecovery, remainingDelay);
      return;
    }
    root.style.display = "block";
    if (minimizedScopes.has(scope)) {
      const reopen = document.createElement("button");
      reopen.type = "button";
      reopen.textContent = "Unconfirmed message";
      reopen.style.cssText = "background:transparent;color:inherit;border:0;font:inherit;padding:0;cursor:pointer";
      reopen.onclick = () => {
        minimizedScopes.delete(scope);
        refreshRecovery();
      };
      root.style.width = "auto";
      root.style.padding = ".55rem .8rem";
      root.append(reopen);
      return;
    }
    root.style.width = "min(30rem,calc(100vw - 1.6rem))";
    root.style.padding = "1rem 1.15rem";
    const header = document.createElement("div");
    header.style.cssText = "display:flex;align-items:center;justify-content:space-between;gap:1rem";
    const label = document.createElement("div");
    label.textContent = "Unconfirmed message";
    label.style.fontWeight = "600";
    const closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.textContent = "×";
    closeButton.setAttribute("aria-label", "Minimize recovery dialog");
    closeButton.title = "Minimize";
    closeButton.style.cssText = "background:transparent;color:inherit;border:0;font:inherit;font-size:1.35rem;line-height:1;padding:0;cursor:pointer";
    closeButton.onclick = () => {
      minimizedScopes.add(scope);
      refreshRecovery();
    };
    header.append(label, closeButton);
    const description = document.createElement("div");
    description.textContent = "This message has not yet been confirmed by Codex Server.";
    description.style.cssText = "margin-top:.35rem;font-size:.9rem;opacity:.8";
    const actions = document.createElement("div");
    actions.style.cssText = "display:flex;gap:.4rem;margin-top:.45rem";
    const copyButton = document.createElement("button");
    copyButton.type = "button";
    copyButton.textContent = "Copy";
    copyButton.style.cssText = "background:transparent;color:inherit;border:1px solid currentColor;border-radius:.3rem;padding:.25rem .45rem";
    copyButton.onclick = async () => {
      await copy(pending.text);
      copyButton.textContent = "Copied";
      setTimeout(() => { copyButton.textContent = "Copy"; }, 1200);
    };
    const restoreButton = document.createElement("button");
    restoreButton.type = "button";
    restoreButton.textContent = "Restore to input";
    restoreButton.style.cssText = "background:transparent;color:inherit;border:1px solid currentColor;border-radius:.3rem;padding:.25rem .45rem";
    restoreButton.onclick = () => {
      if (window.codexNomadSurface?.appendToChatInput?.(pending.text, { spacing: "paragraph" })) {
        clearPending(pending.text);
      }
    };
    actions.append(copyButton, restoreButton);
    root.append(header, description, actions);
  };
  document.addEventListener("keydown", (event) => {
    if (event.target !== chatInput() || event.key !== "Enter" || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
    writePending(event.target.value || "");
  }, true);
  document.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("button") : null;
    const root = chatInput()?.closest('[data-testid="stChatInput"]');
    if (!button || !root?.contains(button)) return;
    const label = `${button.getAttribute("aria-label") || ""} ${button.dataset.testid || ""}`.toLowerCase();
    if (button.type === "submit" || label.includes("send") || label.includes("submit")) {
      writePending(chatInput()?.value || "");
    }
  }, true);
  window.codexNomadSurface = window.codexNomadSurface || {};
  window.codexNomadSurface.clearPendingChatMessage = clearPending;
  window.codexNomadSurface.setChatOutboxScope = (nextScope) => {
    scope = String(nextScope || "");
    refreshRecovery();
  };
  window.codexNomadSurface.refreshPendingChatMessage = refreshRecovery;
  refreshRecovery();
})();
