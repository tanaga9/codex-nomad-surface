(() => {
  if (window.__nomadSelectboxMobileGuardInstalled) {
    return;
  }
  window.__nomadSelectboxMobileGuardInstalled = true;

  const isTouchLike = () =>
    window.matchMedia?.("(pointer: coarse)").matches ||
    window.innerWidth <= 760 ||
    navigator.maxTouchPoints > 0;

  const lockInput = (input) => {
    input.readOnly = true;
    input.inputMode = "none";
    input.autocomplete = "off";
    input.setAttribute("aria-readonly", "true");
  };

  const guardSelectboxInputs = () => {
    if (!isTouchLike()) {
      return;
    }

    document.querySelectorAll('[data-baseweb="select"] input').forEach((input) => {
      if (!(input instanceof HTMLInputElement)) {
        return;
      }
      lockInput(input);
    });
  };

  document.addEventListener(
    "focusin",
    (event) => {
      const input = event.target;
      if (
        !isTouchLike() ||
        !(input instanceof HTMLInputElement) ||
        !input.closest('[data-baseweb="select"]')
      ) {
        return;
      }
      lockInput(input);
    },
    true,
  );

  guardSelectboxInputs();
  new MutationObserver(guardSelectboxInputs).observe(document.body, {
    childList: true,
    subtree: true,
  });
})();
