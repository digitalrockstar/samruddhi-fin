(() => {
  const root = document.documentElement;
  const availableThemes = ["mint", "blue", "violet", "peach", "dark"];
  const applyTheme = (theme) => {
    if (!availableThemes.includes(theme)) return;
    root.setAttribute("data-theme", theme);
    document.querySelectorAll("[data-theme-choice]").forEach((button) => {
      const selected = button.dataset.themeChoice === theme;
      button.setAttribute("aria-pressed", selected ? "true" : "false");
      button.classList.toggle("is-selected", selected);
    });
    try { window.localStorage.setItem("samruddhi-theme", theme); } catch (_) {}
    const themeMeta = document.querySelector('meta[name="theme-color"]');
    if (themeMeta) {
      const colors = { mint: "#f4f7f2", blue: "#f3f6fb", violet: "#f7f4fb", peach: "#fbf5ef", dark: "#171b1a" };
      themeMeta.setAttribute("content", colors[theme] || colors.mint);
    }
  };
  let savedTheme = "mint";
  try { savedTheme = window.localStorage.getItem("samruddhi-theme") || "mint"; } catch (_) {}
  applyTheme(savedTheme);
  // Bind directly to the static theme controls. This avoids delegated-click
  // handlers elsewhere in the app swallowing the theme selection event.
  document.querySelectorAll("[data-theme-choice]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      applyTheme(button.dataset.themeChoice);
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
    const descriptionInput = form.querySelector('input[name="description"]');

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
    const autoPickCashbackScheme = () => {
      filterCashbackSchemes();
      if (!schemeSelect || schemeSelect.value || rateInput?.value || destinationSelect?.value) return;
      const description = (descriptionInput?.value || "").toLowerCase();
      const accountId = accountSelect?.value;
      const options = [...schemeSelect.options].filter((option) =>
        option.value && option.dataset.accountId === accountId &&
        option.dataset.keyword && description.includes(option.dataset.keyword.toLowerCase())
      ).sort((a, b) => (b.dataset.keyword || "").length - (a.dataset.keyword || "").length);
      if (options.length) {
        schemeSelect.value = options[0].value;
        filterCashbackSchemes();
      }
    };
    schemeSelect?.addEventListener("change", filterCashbackSchemes);
    accountSelect?.addEventListener("change", autoPickCashbackScheme);
    descriptionInput?.addEventListener("input", autoPickCashbackScheme);
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

  // Local inline SVG icons replace platform-dependent text glyphs in controls.
  const iconPaths = {
    save:'<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><path d="M17 21v-8H7v8M7 3v5h8"/>',
    x:'<path d="m18 6-12 12M6 6l12 12"/>',
    pencil:'<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L8 18l-4 1 1-4Z"/>',
    archive:'<rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v11a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8M10 12h4"/>',
    history:'<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/>',
    trash:'<path d="M3 6h18M8 6V4h8v2m3 0-1 14H6L5 6m4 4v6m6-6v6"/>',
    copy:'<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
    plus:'<path d="M12 5v14M5 12h14"/>',
    check:'<path d="m5 12 4 4L19 6"/>',
    home:'<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7h-6v7H4a1 1 0 0 1-1-1Z"/>',
    activity:'<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    wallet:'<rect x="3" y="5" width="18" height="15" rx="2"/><path d="M3 8h18M16 14h2"/>',
    card:'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18"/>',
    budget:'<path d="M4 19V5m0 14h17M8 16v-5m4 5V7m4 9V9m4 7v-3"/>',
    target:'<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/>',
    users:'<path d="M16 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="10" cy="7" r="4"/><path d="M20 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
    tag:'<path d="M20 13 11 22l-9-9V2h11Z"/><circle cx="7" cy="7" r="1"/>',
    settings:'<circle cx="12" cy="12" r="3"/><path d="M19 13.5a7.7 7.7 0 0 0 0-3l2-1.5-2-3.5-2.3.9a8 8 0 0 0-2.6-1.5L13.7 2h-3.4l-.4 2.9a8 8 0 0 0-2.6 1.5L5 5.5 3 9l2 1.5a7.7 7.7 0 0 0 0 3L3 15l2 3.5 2.3-.9a8 8 0 0 0 2.6 1.5l.4 2.9h3.4l.4-2.9a8 8 0 0 0 2.6-1.5l2.3.9 2-3.5Z"/>',
    shield:'<path d="M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Z"/><path d="m9 12 2 2 4-4"/>',
    arrows:'<path d="m7 7 4-4 4 4M11 3v12M17 17l-4 4-4-4M13 21V9"/>',
    import:'<path d="M12 3v12m-5-5 5 5 5-5"/><path d="M5 17v4h14v-4"/>',
    export:'<path d="M12 15V3m-5 5 5-5 5 5"/><path d="M5 17v4h14v-4"/>'
  };
  const renderIcon = (el, name) => {
    if (!el || !iconPaths[name]) return;
    el.innerHTML = '<svg class="ui-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' + iconPaths[name] + '</svg>';
    el.classList.add("has-svg-icon");
  };
  // Action controls use descriptive text labels rather than icon-only glyphs.
  document.querySelectorAll(".nav-icon, .feature-icon").forEach((el) => {
    const label = (el.closest("a")?.innerText || el.parentElement?.innerText || "").toLowerCase();
    let name = "tag";
    if (/overview|home/.test(label)) name = "home";
    else if (/transaction|activity/.test(label)) name = "activity";
    else if (/account/.test(label)) name = "wallet";
    else if (/credit card/.test(label)) name = "card";
    else if (/budget/.test(label)) name = "budget";
    else if (/goal/.test(label)) name = "target";
    else if (/subscription/.test(label)) name = "calendar";
    else if (/approval|cashback/.test(label)) name = "check";
    else if (/people|member/.test(label)) name = "users";
    else if (/categor/.test(label)) name = "tag";
    else if (/setting/.test(label)) name = "settings";
    else if (/password|security/.test(label)) name = "shield";
    else if (/more/.test(label)) name = "settings";
    renderIcon(el, name);
  });
  document.querySelectorAll(".txn-icon").forEach((el) => {
    renderIcon(el, el.classList.contains("income") ? "import" : el.classList.contains("transfer") ? "arrows" : "export");
  });
  document.querySelectorAll(".account-avatar.large").forEach((el) => renderIcon(el, "card"));
  document.querySelectorAll(".account-avatar:not(.large)").forEach((el) => { if (/[↗↘↙↖]/.test(el.textContent)) renderIcon(el, "export"); });
  document.querySelectorAll(".mobile-footer a:not(.mobile-add)").forEach((el) => {
    const label = (el.innerText || "").toLowerCase();
    const name = label.includes("home") ? "home" : label.includes("activity") ? "activity" : label.includes("budgets") ? "budget" : "settings";
    const span = document.createElement("span"); span.className = "mobile-footer-icon";
    renderIcon(span, name); el.insertBefore(span, el.firstChild);
    if (span.nextSibling?.nodeType === Node.TEXT_NODE) span.nextSibling.textContent = "";
  });


  document.querySelectorAll("form[data-confirm]").forEach((formElement) => {
    formElement.addEventListener("submit", (event) => {
      if (!window.confirm(formElement.dataset.confirm)) event.preventDefault();
    });
  });
})();
