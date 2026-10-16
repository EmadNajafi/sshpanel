(() => {
  const accountData = document.getElementById("site-search-accounts");
  const userRules = document.getElementById("site-policy-users");
  const form = document.querySelector("[data-site-user-form]");
  if (!accountData || !userRules || !form) return;

  const accounts = JSON.parse(accountData.textContent);
  const rules = JSON.parse(userRules.textContent);
  const search = form.querySelector("[data-site-search]");
  const accountId = form.querySelector("[data-site-account-id]");
  const results = form.querySelector("[data-site-results]");
  const selection = form.querySelector("[data-site-selection]");
  const domains = form.querySelector("[data-user-sites]");
  const save = form.querySelector("[data-site-save]");
  const remove = form.querySelector("[data-site-remove]");
  const t = (text) => window.panelT ? window.panelT(text) : text;
  let selected = null;

  const closeResults = () => {
    results.hidden = true;
    search.setAttribute("aria-expanded", "false");
  };
  const updateSelection = (account) => {
    selected = account;
    accountId.value = account ? String(account.id) : "";
    const custom = account && Object.hasOwn(rules, account.username);
    domains.value = custom ? rules[account.username].join("\n") : "";
    selection.textContent = t(account ? (custom ? "Custom rule active" : "Uses the global list") : "Select a user to see their firewall rule.");
    save.disabled = !account;
    remove.disabled = !custom;
    search.setCustomValidity("");
  };
  const choose = (account) => {
    search.value = account.username;
    updateSelection(account);
    search.focus();
    closeResults();
  };
  const renderResults = () => {
    results.replaceChildren();
    const term = search.value.trim().toLowerCase();
    if (!term) return closeResults();
    const matches = accounts.filter((account) => account.username.toLowerCase().includes(term)).slice(0, 8);
    if (!matches.length) {
      const empty = document.createElement("p");
      empty.textContent = t("No users found");
      results.append(empty);
    }
    for (const account of matches) {
      const option = document.createElement("button");
      option.type = "button";
      option.className = "website-search-option";
      option.setAttribute("role", "option");
      option.setAttribute("data-username", account.username);
      option.textContent = account.username;
      option.addEventListener("click", () => choose(account));
      results.append(option);
    }
    results.hidden = false;
    search.setAttribute("aria-expanded", "true");
  };

  search.addEventListener("input", () => {
    const exact = accounts.find((account) => account.username.toLowerCase() === search.value.trim().toLowerCase());
    updateSelection(exact || null);
    renderResults();
  });
  search.addEventListener("focus", renderResults);
  search.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeResults();
    if (event.key === "ArrowDown") {
      const first = results.querySelector("button");
      if (first && !results.hidden) { event.preventDefault(); first.focus(); }
    }
    if (event.key === "Enter" && !selected) {
      const first = results.querySelector("button");
      if (first && !results.hidden) { event.preventDefault(); first.click(); }
    }
  });
  results.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { closeResults(); search.focus(); }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      const options = Array.from(results.querySelectorAll("button"));
      const index = options.indexOf(document.activeElement);
      const next = options[index + (event.key === "ArrowDown" ? 1 : -1)];
      if (next) { event.preventDefault(); next.focus(); }
    }
  });
  document.addEventListener("pointerdown", (event) => {
    if (!form.querySelector(".website-user-search").contains(event.target)) closeResults();
  });
  form.addEventListener("submit", (event) => {
    if (selected && accountId.value === String(selected.id) && search.value.trim().toLowerCase() === selected.username.toLowerCase()) return;
    event.preventDefault();
    search.setCustomValidity(t("Select a user from the search results."));
    search.reportValidity();
  });
  updateSelection(null);
})();
