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
    const expenseFields = form.querySelectorAll(".expense-only-field");
    const subscriptionCheck = form.querySelector("#make-subscription");
    const subscriptionFields = form.querySelector(".subscription-fields");
    const subscriptionFrequency = form.querySelector("#subscription-frequency");
    const intervalFields = form.querySelectorAll(".subscription-interval-field");
    const splitMethod = form.querySelector("#split-method");
    const schemeSelect = form.querySelector("#cashback-scheme");
    const accountSelect = form.querySelector("#txn-account");
    const rateInput = form.querySelector("#cashback-rate");
    const destinationSelect = form.querySelector("#cashback-destination");
    const delayInput = form.querySelector("#cashback-delay");

    const updateKindAndExtras = () => {
      updateKind();
      const kind = form.querySelector('input[name="kind"]:checked')?.value || "expense";
      expenseFields.forEach((field) => { field.hidden = kind !== "expense"; });
      const everyN = subscriptionFrequency?.value === "every_n_weeks";
      intervalFields.forEach((field) => { field.hidden = !everyN; });
      if (subscriptionFields) subscriptionFields.hidden = !subscriptionCheck?.checked;
      updateBeneficiaryRows();
      filterCashbackSchemes();
    };
    const updateBeneficiaryRows = () => {
      const selected = new Set([...form.querySelectorAll('input[name="beneficiary_ids"]:checked')].map((input) => input.value));
      form.querySelectorAll("[data-beneficiary-row]").forEach((row) => {
        row.hidden = !selected.has(row.dataset.beneficiaryRow);
      });
      const method = splitMethod?.value || "equal";
      form.querySelectorAll('[name^="beneficiary_amount_"]').forEach((input) => {
        input.disabled = method !== "amount" || !selected.has(input.name.replace("beneficiary_amount_", ""));
      });
      form.querySelectorAll('[name^="beneficiary_percent_"]').forEach((input) => {
        input.disabled = method !== "percentage" || !selected.has(input.name.replace("beneficiary_percent_", ""));
      });
    };
    const filterCashbackSchemes = () => {
      if (!schemeSelect || !accountSelect) return;
      const accountId = accountSelect.value;
      [...schemeSelect.options].forEach((option) => {
        if (!option.value) return;
        const valid = option.dataset.accountId === accountId;
        option.hidden = !valid;
        if (!valid && option.selected) schemeSelect.value = "";
      });
      const selected = schemeSelect.selectedOptions[0];
      if (selected?.value) {
        if (rateInput) rateInput.value = selected.dataset.rate || "";
        if (destinationSelect) destinationSelect.value = selected.dataset.destinationId || "";
        if (delayInput) delayInput.value = selected.dataset.delay || "0";
      }
    };
    subscriptionCheck?.addEventListener("change", updateKindAndExtras);
    subscriptionFrequency?.addEventListener("change", updateKindAndExtras);
    splitMethod?.addEventListener("change", updateBeneficiaryRows);
    form.querySelectorAll('input[name="beneficiary_ids"]').forEach((input) => input.addEventListener("change", updateBeneficiaryRows));
    schemeSelect?.addEventListener("change", filterCashbackSchemes);
    accountSelect?.addEventListener("change", filterCashbackSchemes);
    updateKindAndExtras();
  }

  const budgetOwner = document.querySelector("#budget-owner");
  const budgetMember = document.querySelector(".member-budget-field");
  const goalOwner = document.querySelector("#goal-owner");
  const goalMember = document.querySelector(".member-goal-field");
  const updateOwnerField = (select, field) => {
    if (select && field) field.hidden = select.value !== "individual";
  };
  budgetOwner?.addEventListener("change", () => updateOwnerField(budgetOwner, budgetMember));
  goalOwner?.addEventListener("change", () => updateOwnerField(goalOwner, goalMember));
  updateOwnerField(budgetOwner, budgetMember);
  updateOwnerField(goalOwner, goalMember);

  const budgetPeriod = document.querySelector("#budget-period");
  const budgetSpread = document.querySelector("#budget-spread");
  const updateBudgetSpread = () => {
    const row = budgetSpread?.closest(".check-row");
    if (row) row.hidden = budgetPeriod?.value !== "annual";
    if (budgetPeriod?.value !== "annual" && budgetSpread) budgetSpread.checked = false;
  };
  budgetPeriod?.addEventListener("change", updateBudgetSpread);
  updateBudgetSpread();

  const genericSubscriptionFrequency = document.querySelector("form select[name='frequency']");
  const genericInterval = document.querySelector(".subscription-interval-field");
  genericSubscriptionFrequency?.addEventListener("change", () => {
    if (genericInterval) genericInterval.hidden = genericSubscriptionFrequency.value !== "every_n_weeks";
  });
  if (genericSubscriptionFrequency && genericInterval) genericInterval.hidden = genericSubscriptionFrequency.value !== "every_n_weeks";
\n  document.querySelectorAll(".cc-config-form").forEach((configForm) => {
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
