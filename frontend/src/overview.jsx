import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

function SummaryIcon({ type }) {
  const shared = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8,
    strokeLinecap: "round", strokeLinejoin: "round" };
  if (type === "total") return <svg {...shared}><circle cx="9" cy="8" r="3"/><path d="M3.5 19v-1.5a5.5 5.5 0 0 1 11 0V19H3.5Z"/><path d="M16 5.5a2.5 2.5 0 0 1 0 5M17 14a4 4 0 0 1 3.5 4v1"/></svg>;
  if (type === "online") return <svg {...shared}><circle cx="12" cy="16" r="2"/><path d="M7.8 11.8a6 6 0 0 1 8.4 0M4.5 8.5a10.5 10.5 0 0 1 15 0"/></svg>;
  return <svg {...shared}><circle cx="9" cy="8" r="3"/><path d="M3.5 19v-1.5a5.5 5.5 0 0 1 11 0V19H3.5ZM15 16.5h5"/></svg>;
}

function AccountOverview({ element }) {
  const [filter, setFilter] = useState(() => new URLSearchParams(window.location.search).get("status") || "total");
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
    const updateFilter = (event) => setFilter(event.detail?.filter || "total");
    window.addEventListener("sshvpn:account-filter-changed", updateFilter);
    return () => {
      window.removeEventListener("sshvpn:account-summary", update);
      window.removeEventListener("sshvpn:account-filter-changed", updateFilter);
    };
  }, []);

  const cards = [
    { key: "total", title: "Total users", detail: "All VPN accounts" },
    { key: "online", title: "Online now", detail: "Connected users, updated live" },
    { key: "inactive", title: "Inactive users", detail: "Disabled, expired or out of traffic" },
  ];
  return <section className="summary-grid" aria-label="VPN account overview">
    {cards.map(({ key, title, detail }) =>
      <button className={`summary-card summary-${key}`} type="button" data-summary-filter={key}
              aria-pressed={filter === key} key={key}>
        <span className="summary-icon" aria-hidden="true"><SummaryIcon type={key} /></span>
        <span className="summary-label">{title}</span>
        <strong className="summary-value">{summary[key] ?? "—"}</strong>
        <span className="summary-detail">{detail}</span>
        <span className="summary-open" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14m-6-6 6 6-6 6" /></svg></span>
      </button>
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
    refresh();
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
