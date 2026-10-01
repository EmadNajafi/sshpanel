(() => {
  const themeButton = document.querySelector("[data-theme-toggle]");
  if (themeButton) {
    const updateThemeButton = () => {
      const dark = document.documentElement.dataset.theme === "dark";
      themeButton.setAttribute("aria-pressed", String(dark));
      themeButton.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
      themeButton.querySelector("[data-theme-label]").textContent = dark ? "Light mode" : "Dark mode";
    };
    updateThemeButton();
    themeButton.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("sshvpn-theme", next); } catch (_) { /* Private browsing may block storage. */ }
      updateThemeButton();
    });
  }

  const button = document.querySelector("[data-menu-toggle]");
  const menu = document.getElementById("panel-menu");
  if (!button || !menu) return;
  const close = () => {
    if (menu.open) menu.close();
  };
  button.addEventListener("click", () => {
    menu.showModal();
    button.setAttribute("aria-expanded", "true");
    button.setAttribute("aria-label", "Close navigation menu");
  });
  menu.querySelector("[data-menu-close]").addEventListener("click", close);
  menu.addEventListener("close", () => {
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-label", "Open navigation menu");
    button.focus();
  });
  menu.addEventListener("click", (event) => {
    if (event.target === menu) close();
  });
})();
