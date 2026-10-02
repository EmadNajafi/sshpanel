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

  const extendDialog = document.getElementById("extend-account-dialog");
  document.querySelectorAll("[data-extend-account]").forEach((button) => {
    button.addEventListener("click", () => {
      extendDialog.querySelector("[data-extend-name]").textContent = button.dataset.username;
      extendDialog.querySelector("[data-extend-form]").action = button.dataset.action;
      extendDialog.showModal();
    });
  });

  const feedback = document.querySelector("[data-action-feedback]");
  let feedbackTimer;
  function showFeedback(message, isError = false) {
    feedback.textContent = message;
    feedback.classList.toggle("is-error", isError);
    feedback.hidden = false;
    window.clearTimeout(feedbackTimer);
    feedbackTimer = window.setTimeout(() => { feedback.hidden = true; }, 4000);
  }

  async function copyText(value) {
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(value);
        return true;
      } catch (_error) {
        // HTTP panels use the selection based browser fallback below.
      }
    }
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.append(textarea);
    textarea.focus();
    textarea.select();
    let copied = false;
    try { copied = document.execCommand("copy"); } catch (_error) { /* show manual fallback */ }
    textarea.remove();
    return copied;
  }

  function formatPersianExpiry(isoValue) {
    if (!isoValue) return "نامحدود";
    const date = new Date(isoValue);
    if (Number.isNaN(date.getTime())) throw new Error("Expiry date is unavailable.");
    const formatter = new Intl.DateTimeFormat("fa-IR-u-ca-persian", {
      timeZone: "Asia/Tehran", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    });
    const parts = Object.fromEntries(formatter.formatToParts(date).map(({ type, value }) => [type, value]));
    return `${parts.year}/${parts.month}/${parts.day} ${parts.hour}:${parts.minute} (تهران)`;
  }

  document.querySelectorAll("[data-copy-account]").forEach((button) => {
    button.addEventListener("click", async () => {
      const row = button.closest("[data-account-username]");
      button.disabled = true;
      try {
        const response = await fetch(button.dataset.passwordUrl, {
          credentials: "same-origin", headers: { Accept: "application/json" }, cache: "no-store",
        });
        if (!response.ok) throw new Error("Could not load the password.");
        const { password } = await response.json();
        if (password === null || password === undefined) throw new Error("Password unavailable. Set a new password first.");
        const details = [
          `Host: ${window.location.hostname}`,
          `SSH port: ${document.querySelector("[data-ssh-port]").dataset.sshPort}`,
          `Username: ${row.dataset.accountUsername}`,
          `Password: ${password}`,
          row.dataset.validDays ? `شروع اعتبار: اولین اتصال موفق (${row.dataset.validDays} روز)` :
            `تاریخ انقضا (شمسی): ${formatPersianExpiry(row.dataset.expiresAt)}`,
          `Connection limit: ${button.dataset.connections || "Unlimited"}`,
          `Traffic limit: ${row.dataset.trafficDisplay || "Unlimited"}`,
        ].join("\n");
        if (await copyText(details)) {
          showFeedback(`Connection details for ${row.dataset.accountUsername} copied.`);
        } else {
          const dialog = document.getElementById("copy-account-dialog");
          const field = dialog.querySelector("[data-copy-details]");
          field.value = details;
          dialog.showModal();
          field.focus();
          field.select();
          showFeedback("Select and copy the connection details.", true);
        }
      } catch (error) {
        showFeedback(error.message, true);
      } finally {
        button.disabled = false;
      }
    });
  });
  document.getElementById("copy-account-dialog").addEventListener("close", (event) => {
    event.target.querySelector("[data-copy-details]").value = "";
  });

  const usagePanel = document.querySelector("[data-usage-url]");
  if (!usagePanel) return;
  let usageLoading = false;
  async function refreshUsage() {
    if (usageLoading || document.hidden) return;
    usageLoading = true;
    try {
      const response = await fetch(usagePanel.dataset.usageUrl, {
        credentials: "same-origin", headers: { Accept: "application/json" }, cache: "no-store",
      });
      if (!response.ok) return;
      const payload = await response.json();
      if (payload.summary) window.dispatchEvent(new CustomEvent("sshvpn:account-summary", { detail: payload.summary }));
      if (payload.activated?.some((username) => [...usagePanel.querySelectorAll("[data-account-username]")]
        .some((row) => row.dataset.accountUsername === username && row.dataset.validDays))) {
        window.location.reload();
        return;
      }
      if (!payload.available) return;
      usagePanel.querySelectorAll("[data-account-username]").forEach((row) => {
        const usage = payload.accounts[row.dataset.accountUsername];
        const onlineCell = row.querySelector("[data-online-status]");
        const usageCell = row.querySelector("[data-usage-cell]");
        const nameButton = row.querySelector(".account-name-button");
        if (!usage) {
          onlineCell.textContent = "—";
          usageCell.textContent = "Unavailable";
          nameButton.disabled = true;
          return;
        }
        const isOnline = usage.connections > 0;
        const status = document.createElement(isOnline ? "button" : "span");
        status.className = `state ${isOnline ? "state-active online-trigger" : "state-disabled"}`;
        if (isOnline) {
          status.type = "button";
          status.dataset.sessionTrigger = "";
        }
        status.textContent = usage.connections > 0 ? `${usage.connections} online` : "Offline";
        onlineCell.replaceChildren(status);
        nameButton.disabled = !isOnline;
        const total = document.createElement("strong");
        total.className = "usage-total";
        total.textContent = row.dataset.trafficDisplay ? `${usage.total} / ${row.dataset.trafficDisplay}` : usage.total;
        const detail = document.createElement("small");
        detail.className = "table-subtext";
        detail.textContent = `↑ ${usage.upload} · ↓ ${usage.download}`;
        usageCell.replaceChildren(total, detail);
        if (row.dataset.trafficLimit && row.dataset.enabled === "true" &&
            (!row.dataset.expiresAt || new Date(row.dataset.expiresAt) > new Date())) {
          const exhausted = usage.total_bytes >= Number(row.dataset.trafficLimit);
          const state = document.createElement("span");
          state.className = `state ${exhausted ? "state-expired" : row.dataset.validDays ? "state-disabled" : "state-active"}`;
          state.textContent = exhausted ? "Traffic exhausted" : row.dataset.validDays ? "Awaiting first connection" : "Active";
          row.querySelector("[data-account-state]").replaceChildren(state);
        }
      });
    } catch (_error) {
      // Keep the previous readings if a refresh fails.
    } finally {
      usageLoading = false;
    }
  }
  refreshUsage();
  window.setInterval(refreshUsage, 15000);

  const sessionsDialog = document.getElementById("sessions-dialog");
  usagePanel.addEventListener("click", async (event) => {
    const trigger = event.target.closest("[data-session-trigger]");
    if (!trigger || trigger.disabled) return;
    const row = trigger.closest("[data-account-username]");
    if (!row) return;
    const username = row.dataset.accountUsername;
    const list = sessionsDialog.querySelector("[data-sessions-list]");
    sessionsDialog.querySelector("[data-sessions-name]").textContent = username;
    list.replaceChildren();
    const message = document.createElement("li");
    message.textContent = "Loading active connections…";
    list.append(message);
    sessionsDialog.showModal();
    try {
      const response = await fetch(usagePanel.dataset.usageUrl, {
        credentials: "same-origin", headers: { Accept: "application/json" }, cache: "no-store",
      });
      if (!response.ok) throw new Error("Request failed");
      const payload = await response.json();
      if (!payload.available) throw new Error("Usage unavailable");
      const ips = payload.accounts?.[username]?.ips ?? [];
      list.replaceChildren();
      if (!ips.length) {
        const empty = document.createElement("li");
        empty.textContent = "No active connections now.";
        list.append(empty);
      } else {
        ips.forEach((ip, index) => {
          const item = document.createElement("li");
          const label = document.createElement("span");
          label.textContent = `Connection ${index + 1}`;
          const address = document.createElement("code");
          address.textContent = ip ?? "IP unavailable — reconnect needed";
          item.append(label, address);
          list.append(item);
        });
      }
    } catch (_error) {
      list.replaceChildren();
      const error = document.createElement("li");
      error.textContent = "Could not load active IP addresses.";
      list.append(error);
    }
  });
})();
