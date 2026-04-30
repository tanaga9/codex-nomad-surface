(() => {
  const mountedRoots = new WeakSet();
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

  const appendToChatInputFallback = (addition, options = {}) => {
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

  const interpolateTemplate = (template, values) =>
    template.replace(/\{{1,2}([a-zA-Z0-9_-]+)\}{1,2}/g, (_, key) => values[key] ?? "");

  const getFieldNodes = (root, fieldId) =>
    Array.from(root.querySelectorAll(`[data-promptform-field="${fieldId}"]`));

  const readFieldValue = (root, field) => {
    const nodes = getFieldNodes(root, field.id);
    if (field.type === "checkbox") {
      return nodes[0] instanceof HTMLInputElement ? nodes[0].checked : false;
    }
    if (field.type === "radio") {
      const checked = nodes.find(
        (node) => node instanceof HTMLInputElement && node.checked,
      );
      return checked instanceof HTMLInputElement ? checked.value : "";
    }

    const node = nodes[0];
    if (
      node instanceof HTMLInputElement ||
      node instanceof HTMLTextAreaElement ||
      node instanceof HTMLSelectElement
    ) {
      return node.value ?? "";
    }
    return "";
  };

  const getSubmissionValue = (field, value) => {
    if (field.type === "checkbox") {
      return value
        ? field.checked_value || "true"
        : field.unchecked_value || "false";
    }
    return String(value ?? "");
  };

  const setStatus = (root, message) => {
    const node = root.querySelector("[data-promptform-status]");
    if (node instanceof HTMLElement) {
      node.textContent = message;
    }
  };

  const mount = (root, schema) => {
    if (!(root instanceof HTMLElement) || mountedRoots.has(root)) {
      return;
    }
    mountedRoots.add(root);

    const form = root.querySelector("[data-promptform-root]");
    if (!(form instanceof HTMLFormElement)) {
      return;
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();

      const submissionValues = {};
      const missingLabels = [];

      for (const field of schema.fields || []) {
        const rawValue = readFieldValue(root, field);
        const submitValue = getSubmissionValue(field, rawValue);
        submissionValues[field.id] = submitValue;
        if (field.required && !String(submitValue ?? "").trim()) {
          missingLabels.push(field.label || field.id);
        }
      }

      if (missingLabels.length > 0) {
        setStatus(root, `Please fill: ${missingLabels.join(", ")}`);
        return;
      }

      const text = interpolateTemplate(schema.template || "", submissionValues).trim();
      if (!text) {
        setStatus(root, "Nothing to insert.");
        return;
      }

      const appendToChatInput =
        window.codexNomadSurface?.appendToChatInput || appendToChatInputFallback;
      const appended = appendToChatInput(text, {
        spacing: schema.append_spacing || "paragraph",
      });
      setStatus(root, appended ? "Added to chat input." : "Chat input was not found.");
    });
  };

  window.PromptForm = window.PromptForm || {};
  window.PromptForm.mount = mount;
})();
