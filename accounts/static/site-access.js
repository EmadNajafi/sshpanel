(() => {
  const users = document.getElementById("site-policy-users");
  const select = document.querySelector("[data-site-account]");
  const mode = document.querySelector("[data-site-mode]");
  const domains = document.querySelector("[data-user-sites]");
  if (!users || !select || !mode || !domains) return;
  const rules = JSON.parse(users.textContent);
  const refresh = () => {
    const username = select.selectedOptions[0]?.dataset.username;
    const custom = username && Object.hasOwn(rules, username);
    mode.value = custom ? "custom" : "inherit";
    domains.value = custom ? rules[username].join("\n") : "";
    domains.disabled = mode.value === "inherit";
  };
  select.addEventListener("change", refresh);
  mode.addEventListener("change", () => { domains.disabled = mode.value === "inherit"; });
  refresh();
})();
