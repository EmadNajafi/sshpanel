(() => {
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
