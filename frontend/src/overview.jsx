import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

function AccountOverview({ element }) {
  const [summary, setSummary] = useState({
    total: Number(element.dataset.total) || 0,
    online: element.dataset.online === "" ? null : Number(element.dataset.online),
    inactive: Number(element.dataset.inactive) || 0,
  });

  useEffect(() => {
    const update = (event) => {
      if (event.detail && typeof event.detail.total === "number") setSummary(event.detail);
    };
    window.addEventListener("sshvpn:account-summary", update);
    return () => window.removeEventListener("sshvpn:account-summary", update);
  }, []);

  const cards = [
    { key: "total", title: "Total users", detail: "All VPN accounts", icon: "◎" },
    { key: "online", title: "Online now", detail: "Connected users, updated live", icon: "↗" },
    { key: "inactive", title: "Inactive users", detail: "Disabled or expired", icon: "◌" },
  ];
  return <section className="summary-grid" aria-label="VPN account overview">
    {cards.map(({ key, title, detail, icon }) =>
      <article className={`summary-card summary-${key}`} key={key}>
        <span className="summary-icon" aria-hidden="true">{icon}</span>
        <span className="summary-label">{title}</span>
        <strong className="summary-value">{summary[key] ?? "—"}</strong>
        <span className="summary-detail">{detail}</span>
      </article>
    )}
  </section>;
}

function initialMetric(element, name) {
  const value = element.dataset[name];
  return {
    percent: value === "" ? null : Number(value),
    detail: element.dataset[`${name}Detail`] || "Waiting for server data",
  };
}

function ServerResources({ element }) {
  const [metrics, setMetrics] = useState(() => ({
    cpu: initialMetric(element, "cpu"),
    memory: initialMetric(element, "memory"),
    disk: initialMetric(element, "disk"),
  }));

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      if (document.hidden) return;
      try {
        const response = await fetch(element.dataset.metricsUrl, {
          credentials: "same-origin", headers: { Accept: "application/json" }, cache: "no-store",
        });
        if (!response.ok) return;
        const next = await response.json();
        if (active) setMetrics(next);
      } catch (_) { /* Keep the last reading during a network interruption. */ }
    };
    window.addEventListener("sshvpn:menu-open", refresh);
    const timer = window.setInterval(() => {
      if (document.getElementById("panel-menu")?.open) refresh();
    }, 15000);
    return () => {
      active = false;
      window.removeEventListener("sshvpn:menu-open", refresh);
      window.clearInterval(timer);
    };
  }, [element]);

  return <div className="drawer-resource-list">
    {[["cpu", "CPU"], ["memory", "RAM"], ["disk", "Disk"]].map(([key, label]) => {
      const metric = metrics[key] || {};
      const percent = Number.isFinite(metric.percent) ? Math.max(0, Math.min(100, metric.percent)) : null;
      return <div className={`drawer-resource drawer-resource-${key}`} key={key}>
        <div className="drawer-resource-head"><span>{label}</span><strong>{percent === null ? "—" : `${percent}%`}</strong></div>
        <div className="drawer-resource-track" aria-hidden="true"><span style={{ width: `${percent ?? 0}%` }} /></div>
        <small>{metric.detail || "Unavailable"}</small>
      </div>;
    })}
  </div>;
}

const accountElement = document.getElementById("account-overview-root");
if (accountElement) createRoot(accountElement).render(<AccountOverview element={accountElement} />);

const resourceElement = document.getElementById("server-resources-root");
if (resourceElement) createRoot(resourceElement).render(<ServerResources element={resourceElement} />);
