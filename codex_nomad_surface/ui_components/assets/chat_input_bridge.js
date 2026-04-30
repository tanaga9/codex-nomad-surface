(() => {
  const CHAT_INPUT_SELECTORS = [
    '[data-testid="stChatInputTextArea"]',
  ];

  const getChatInput = () => {
    for (const selector of CHAT_INPUT_SELECTORS) {
      const node = document.querySelector(selector);
      if (node instanceof HTMLTextAreaElement) {
        return node;
      }
    }
    return null;
  };

  const appendToChatInput = (addition, options = {}) => {
    const textarea = getChatInput();
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

  window.codexNomadSurface = window.codexNomadSurface || {};
  window.codexNomadSurface.appendToChatInput = appendToChatInput;
})();
