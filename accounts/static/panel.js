(() => {
  const button = document.querySelector("[data-menu-toggle]");
  const menu = document.getElementById("panel-menu");
  if (!button || !menu) return;
  const close = () => {
    menu.hidden = true;
    button.setAttribute("aria-expanded", "false");
  };
  button.addEventListener("click", () => {
    menu.hidden = !menu.hidden;
    button.setAttribute("aria-expanded", String(!menu.hidden));
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") close(); });
  document.addEventListener("click", (event) => {
    if (!menu.contains(event.target) && !button.contains(event.target)) close();
  });
})();
