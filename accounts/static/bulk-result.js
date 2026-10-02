(() => {
  const panel = document.querySelector("[data-bulk-result]");
  if (!panel) return;
  const rows = [...panel.querySelectorAll("[data-bulk-row]")].map((row) => ({
    username: row.querySelector("[data-username]").textContent,
    password: row.querySelector("[data-password]").textContent,
    referralText: row.querySelector("[data-referral]").textContent,
    expiry: row.querySelector("[data-expiry]").textContent.trim(),
  }));
  const host = panel.dataset.host;
  const port = panel.dataset.port;
  const connections = panel.dataset.connections;
  const traffic = panel.dataset.traffic;
  const details = rows.map((row) =>
    `Host: ${host}\nSSH port: ${port}\nUsername: ${row.username}\nPassword: ${row.password}\nExpiry: ${row.expiry}\nConnection limit: ${connections}\nTraffic limit: ${traffic}\nReferral text: ${row.referralText}`
  ).join("\n\n");
  panel.querySelector("[data-bulk-copy]").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(details);
    } catch (_error) {
      const field = document.createElement("textarea");
      field.value = details;
      field.style.position = "fixed";
      field.style.opacity = "0";
      document.body.append(field);
      field.select();
      try { document.execCommand("copy"); } finally { field.remove(); }
    }
  });
  const csv = (value) => `"${String(value).replaceAll('"', '""')}"`;
  panel.querySelector("[data-bulk-download]").addEventListener("click", () => {
    const lines = [["host", "ssh_port", "username", "password", "expiry", "connection_limit", "traffic_limit", "referral_text"],
      ...rows.map((row) => [host, port, row.username, row.password, row.expiry, connections, traffic, row.referralText])];
    const content = "\uFEFF" + lines.map((values) => values.map(csv).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob([content], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "sshpanel-bulk-users.csv";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  });
})();
