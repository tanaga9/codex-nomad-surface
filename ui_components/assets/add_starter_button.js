(() => {
  const button = document.currentScript.previousElementSibling;
  if (!(button instanceof HTMLButtonElement)) {
    return;
  }

  const starter = __STARTER_JSON__;
  const referenceButtonSelectors = [
    '[data-testid="stBaseButton-secondary"]:not([data-codex-add-starter="true"])',
    '.stButton > button:not([data-codex-add-starter="true"])',
    'button[kind="secondary"]:not([data-codex-add-starter="true"])',
  ];
  const themeSyncObservers = [];

  const syncButtonTheme = () => {
    const referenceButton = referenceButtonSelectors
      .map((selector) => document.querySelector(selector))
      .find((candidate) => candidate instanceof HTMLButtonElement);

    if (!(referenceButton instanceof HTMLButtonElement)) {
      return;
    }

    const computed = window.getComputedStyle(referenceButton);
    const copiedProperties = [
      "background",
      "backgroundColor",
      "border",
      "borderColor",
      "borderRadius",
      "boxShadow",
      "color",
      "fontFamily",
      "fontSize",
      "fontWeight",
      "lineHeight",
      "minHeight",
      "padding",
      "transition",
    ];

    copiedProperties.forEach((property) => {
      button.style[property] = computed[property];
    });
    button.style.width = "100%";
    button.style.display = "inline-flex";
    button.style.alignItems = "center";
    button.style.justifyContent = "center";
    button.style.boxSizing = "border-box";

    if (button.disabled) {
      button.style.cursor = "not-allowed";
      button.style.opacity = "0.55";
    } else {
      button.style.cursor = computed.cursor || "pointer";
      button.style.opacity = "1";
    }
  };

  const observeThemeChanges = () => {
    const observerTargets = [
      document.documentElement,
      document.body,
      ...referenceButtonSelectors
        .map((selector) => document.querySelector(selector))
        .filter((candidate) => candidate instanceof HTMLElement),
    ];

    observerTargets.forEach((target) => {
      if (!(target instanceof HTMLElement)) {
        return;
      }
      const observer = new MutationObserver(() => {
        window.requestAnimationFrame(syncButtonTheme);
      });
      observer.observe(target, {
        attributes: true,
        attributeFilter: ["class", "style", "data-theme"],
      });
      themeSyncObservers.push(observer);
    });
  };

  const appendStarterFallback = (addition, options = {}) => {
    const textarea = document.querySelector('[data-testid="stChatInputTextArea"]');
    if (!(textarea instanceof HTMLTextAreaElement) || !addition) {
      return false;
    }

    const spacing = options.spacing || "paragraph";
    const current = textarea.value ?? "";
    let separator = "";
    if (spacing === "line" && current) {
      separator = "\n";
    } else if (spacing === "paragraph" && current) {
      separator = current.endsWith("\n\n")
        ? ""
        : current.endsWith("\n")
          ? "\n"
          : "\n\n";
    }

    const next = `${current}${separator}${addition}`;
    const valueSetter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value",
    )?.set;
    valueSetter?.call(textarea, next);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    textarea.focus();
    textarea.setSelectionRange(next.length, next.length);
    return true;
  };

  syncButtonTheme();
  observeThemeChanges();
  button.onclick = (event) => {
    if (button.disabled) {
      return;
    }
    event.preventDefault();
    const appendToChatInput =
      window.codexNomadSurface?.appendToChatInput || appendStarterFallback;
    appendToChatInput(
      starter,
      { spacing: "paragraph" },
    );
  };
})();
