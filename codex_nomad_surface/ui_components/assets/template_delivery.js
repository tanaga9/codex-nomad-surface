export default function ({ data, parentElement, setTriggerValue }) {
  const status = document.createElement("span");
  status.setAttribute("role", "status");
  const retry = document.createElement("button");
  retry.textContent = "Retry insertion";
  retry.type = "button";
  retry.style.marginLeft = ".5rem";
  parentElement.append(status, retry);

  // Keep the receipt for the lifetime of this page, including lost acknowledgements
  // and component remounts. Reusing an operation token must never append twice.
  const receipts = window.codexNomadChatInputAppendTokens ||= new Set();
  const attempt = () => {
    if (receipts.has(data.token)) {
      status.textContent = "Added to draft.";
      retry.hidden = true;
      setTriggerValue("ack", data.token);
      return;
    }
    if (!data.enabled) {
      status.textContent = "Insertion paused. Your text is retained.";
      retry.disabled = true;
      return;
    }
    const append = window.codexNomadSurface?.appendToChatInput;
    try {
      if (typeof append === "function" && append(data.text, { spacing: "paragraph" })) {
        receipts.add(data.token);
        status.textContent = "Added to draft.";
        retry.hidden = true;
        setTriggerValue("ack", data.token);
        return;
      }
    } catch (_) {
      // Retain the pending operation; show a recovery action instead of clearing it.
    }
    status.textContent = "Could not insert into the draft. Your text is retained.";
    retry.hidden = false;
  };
  retry.onclick = attempt;
  attempt();
  return () => {
    retry.onclick = null;
    status.remove();
    retry.remove();
  };
}
