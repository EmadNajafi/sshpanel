(() => {
  document.querySelectorAll("[data-reveal-password]").forEach((button) => {
    button.addEventListener("click", async () => {
      const value = button.closest(".password-cell").querySelector("[data-password-value]");
      if (button.dataset.visible === "true") {
        value.textContent = "••••••";
        button.textContent = "Show";
        button.dataset.visible = "false";
        return;
      }
      button.disabled = true;
      try {
        const response = await fetch(button.dataset.passwordUrl, {
          credentials: "same-origin", headers: { Accept: "application/json" }, cache: "no-store",
        });
        if (!response.ok) throw new Error("Request failed");
        const data = await response.json();
        value.textContent = data.password ?? "Unavailable — set a new password";
        button.textContent = "Hide";
        button.dataset.visible = "true";
      } catch (_error) {
        value.textContent = "Could not load password";
      } finally {
        button.disabled = false;
      }
    });
  });
  document.querySelectorAll("[data-open-dialog]").forEach((button) => {
    button.addEventListener("click", () => document.getElementById(button.dataset.openDialog).showModal());
  });
  document.querySelectorAll("[data-close-dialog]").forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog").close());
  });
  document.querySelectorAll("dialog.account-dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  });
  const editDialog = document.getElementById("edit-account-dialog");
  document.querySelectorAll("[data-edit-account]").forEach((button) => {
    button.addEventListener("click", () => {
      editDialog.querySelector("[data-edit-name]").textContent = button.dataset.username;
      editDialog.querySelector("[data-edit-expires]").textContent = button.dataset.expires;
      const form = editDialog.querySelector("[data-edit-form]");
      form.action = button.dataset.action;
      form.reset();
      form.elements.max_connections.value = button.dataset.connections;
      editDialog.showModal();
    });
  });

  const grid = document.querySelector("[data-metrics-url]");
  if (!grid) return;

  let loading = false;
  async function refreshMetrics() {
    if (loading || document.hidden) return;
    loading = true;
    try {
      const response = await fetch(grid.dataset.metricsUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      const metrics = await response.json();
      for (const name of ["cpu", "memory", "disk"]) {
        const card = grid.querySelector(`[data-metric="${name}"]`);
        const metric = metrics[name];
        if (!card || !metric) continue;
        const value = Number.isFinite(metric.percent) ? Math.max(0, Math.min(100, metric.percent)) : null;
        card.querySelector("[data-metric-value]").textContent = value === null ? "—" : `${value}%`;
        card.querySelector("[data-metric-bar]").style.width = `${value ?? 0}%`;
        card.querySelector("[data-metric-detail]").textContent = metric.detail;
      }
    } catch (_error) {
      // Keep the last snapshot if the server cannot answer a refresh.
    } finally {
      loading = false;
    }
  }

  window.setInterval(refreshMetrics, 15000);
})();
