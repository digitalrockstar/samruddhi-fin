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
  const statementForm = document.querySelector('form input[name="action"][value="save_statement"]')?.closest("form");
  if (statementForm) {
    const cardSelect = statementForm.querySelector("#cc-statement-card");
    const statementDate = statementForm.querySelector("#cc-statement-date");
    const dueDate = statementForm.querySelector("#cc-due-date");
    const suggestDueDate = () => {
      if (!cardSelect || !statementDate || !dueDate || !statementDate.value) return;
      const [year, month, day] = statementDate.value.split("-").map(Number);
      const statementDay = Number(day);
      const configuredDueDay = Number(cardSelect.selectedOptions[0]?.dataset.dueDay || 20);
      let dueYear = year;
      let dueMonth = month - 1;
      if (configuredDueDay <= statementDay) {
        dueMonth += 1;
        if (dueMonth > 11) { dueMonth = 0; dueYear += 1; }
      }
      const suggested = new Date(dueYear, dueMonth, configuredDueDay);
      dueDate.value = [
        suggested.getFullYear(),
        String(suggested.getMonth() + 1).padStart(2, "0"),
        String(suggested.getDate()).padStart(2, "0")
      ].join("-");
    };
    cardSelect?.addEventListener("change", suggestDueDate);
    statementDate?.addEventListener("change", suggestDueDate);
    suggestDueDate();
  }
  document.querySelectorAll("form[data-confirm]").forEach((formElement) => {
    formElement.addEventListener("submit", (event) => {
      if (!window.confirm(formElement.dataset.confirm)) event.preventDefault();
    });
  });
})();
