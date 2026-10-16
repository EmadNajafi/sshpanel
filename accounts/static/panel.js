(() => {
  const themeButton = document.querySelector("[data-theme-toggle]");
  if (themeButton) {
    const themeColor = document.querySelector('meta[name="theme-color"]');
    const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
    const savedTheme = () => {
      try { return localStorage.getItem("sshvpn-theme"); } catch (_) { return null; }
    };
    const updateThemeButton = () => {
      const dark = document.documentElement.dataset.theme === "dark";
      const label = dark ? "Switch to light mode" : "Switch to dark mode";
      const translatedLabel = window.panelT ? window.panelT(label) : label;
      themeButton.setAttribute("aria-pressed", String(dark));
      themeButton.setAttribute("aria-label", translatedLabel);
      themeButton.setAttribute("title", translatedLabel);
      if (themeColor) themeColor.content = dark ? "#081b18" : "#f3f6fb";
    };
    const applyTheme = (theme) => {
      document.documentElement.dataset.theme = theme;
      updateThemeButton();
    };
    updateThemeButton();
    themeButton.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      try { localStorage.setItem("sshvpn-theme", next); } catch (_) { /* Private browsing may block storage. */ }
      applyTheme(next);
    });
    systemTheme.addEventListener?.("change", () => {
      if (savedTheme() !== "dark" && savedTheme() !== "light") applyTheme(systemTheme.matches ? "dark" : "light");
    });
    window.addEventListener("storage", (event) => {
      if (event.key === "sshvpn-theme") applyTheme(event.newValue === "dark" || event.newValue === "light" ? event.newValue : (systemTheme.matches ? "dark" : "light"));
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
    window.dispatchEvent(new Event("sshvpn:menu-open"));
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
