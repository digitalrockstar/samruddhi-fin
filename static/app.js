(() => {
  const root = document.documentElement;
  const saved = localStorage.getItem("samruddhi-theme");
  if (["mint", "blue", "violet", "peach"].includes(saved)) root.dataset.theme = saved;
  document.querySelectorAll("[data-theme-choice]").forEach((button) => {
    button.addEventListener("click", () => {
      const theme = button.dataset.themeChoice;
      if (!["mint", "blue", "violet", "peach"].includes(theme)) return;
      root.dataset.theme = theme;
      localStorage.setItem("samruddhi-theme", theme);
    });
  });
  const form = document.querySelector("#transaction-form");
  if (form) {
    const categoryField = form.querySelector(".category-field");
    const transferField = form.querySelector(".transfer-field");
    const categorySelect = form.querySelector('select[name="category_id"]');
    const updateKind = () => {
      const kind = form.querySelector('input[name="kind"]:checked')?.value || "expense";
      transferField.hidden = kind !== "transfer";
      categoryField.hidden = kind === "transfer";
      [...categorySelect.options].forEach((option) => {
        if (!option.value) return;
        option.hidden = option.dataset.kind !== kind;
        if (option.dataset.kind !== kind && option.selected) categorySelect.value = "";
      });
    };
    form.querySelectorAll('input[name="kind"]').forEach((input) => input.addEventListener("change", updateKind));
    updateKind();
  }
  document.querySelectorAll(".cc-config-form").forEach((configForm) => {
    const select = configForm.querySelector("[data-limit-mode]");
    const updateLimitMode = () => {
      configForm.dataset.mode = select?.value === "shared" ? "shared" : "individual";
    };
    select?.addEventListener("change", updateLimitMode);
    updateLimitMode();
  });
  document.querySelectorAll("form[data-confirm]").forEach((formElement) => {
    formElement.addEventListener("submit", (event) => {
      if (!window.confirm(formElement.dataset.confirm)) event.preventDefault();
    });
  });
})();
