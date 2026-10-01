(() => {
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
