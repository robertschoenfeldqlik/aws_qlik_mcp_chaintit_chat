/**
 * Customizes Chainlit's MCP dialog for Qlik Cloud.
 *
 * - Shows "Connect to Qlik Cloud" title
 * - Pre-fills Name with "Qlik Cloud" and type to SSE
 * - Pre-fills Server URL from localStorage or env defaults
 * - Saves credentials to localStorage on Connect
 * - Hides Headers field (not needed for Qlik OAuth)
 */
(function () {
  "use strict";

  // Load saved credentials
  let savedUrl = localStorage.getItem("qlik_mcp_url") || "";
  let savedName = localStorage.getItem("qlik_mcp_name") || "Qlik Cloud";

  // Fetch defaults from server on startup
  fetch("/auth/qlik/defaults").then(r => r.json()).then(d => {
    if (d.tenant_url && !savedUrl) {
      savedUrl = d.tenant_url.replace(/\/$/, "") + "/api/ai/mcp";
      localStorage.setItem("qlik_mcp_url", savedUrl);
    }
  }).catch(() => {});

  function setReactValue(input, value) {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function customizeDialog(dialog) {
    // Only target MCP dialog
    const title = dialog.querySelector("h2, [class*='DialogTitle']");
    if (!title || !title.textContent.includes("MCP")) return;
    if (dialog.dataset.qlikCustomized) return;
    dialog.dataset.qlikCustomized = "true";

    // Change title
    title.textContent = "Connect to Qlik Cloud";

    // Change tab text
    dialog.querySelectorAll("button").forEach(btn => {
      if (btn.textContent.trim() === "Connect an MCP") btn.textContent = "Connect to Qlik";
    });

    // Find inputs and customize
    const inputs = dialog.querySelectorAll("input");
    inputs.forEach(input => {
      const ph = (input.placeholder || "").toLowerCase();

      // Name field — pre-fill with "Qlik Cloud"
      if (ph.includes("stripe") || ph.includes("name")) {
        if (!input.value) setReactValue(input, savedName);
        input.placeholder = "Qlik Cloud";
      }

      // Server URL — pre-fill from localStorage
      if (ph.includes("localhost") || ph.includes("url") || ph.includes("server")) {
        if (!input.value && savedUrl) setReactValue(input, savedUrl);
        input.placeholder = "https://your-tenant.us.qlikcloud.com/api/ai/mcp";
      }

      // Headers — hide it (not needed for Qlik OAuth)
      if (ph.includes("authorization") || ph.includes("header") || ph.includes("bearer")) {
        const parent = input.parentElement;
        if (parent) parent.style.display = "none";
        // Also hide the label
        let prev = input.previousElementSibling || (parent && parent.previousElementSibling);
        if (prev && prev.textContent && prev.textContent.includes("Header")) prev.style.display = "none";
      }
    });

    // Hide labels for Headers
    dialog.querySelectorAll("label, p, span").forEach(el => {
      if (el.textContent.trim().toLowerCase().includes("header")) {
        el.style.display = "none";
        if (el.nextElementSibling) el.nextElementSibling.style.display = "none";
      }
    });

    // Change "Server URL" label to "Qlik MCP URL"
    dialog.querySelectorAll("label, p, span").forEach(el => {
      if (el.textContent.trim() === "Server URL *") el.textContent = "Qlik MCP URL *";
    });

    // Add help link before the buttons
    const btnRow = dialog.querySelector("div:has(> button)") || dialog.querySelector("[class*='DialogFooter']");
    if (btnRow && !dialog.querySelector(".qlik-help-link")) {
      const help = document.createElement("div");
      help.className = "qlik-help-link";
      help.style.cssText = "margin:8px 0 12px 0;";
      help.innerHTML = '<a href="https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Connecting-Qlik-MCP-server.htm" target="_blank" style="font-size:12px;color:#006580;text-decoration:none;">Qlik MCP setup guide</a>';
      btnRow.parentElement.insertBefore(help, btnRow);
    }

    // Save URL to localStorage when Confirm is clicked
    const confirmBtn = Array.from(dialog.querySelectorAll("button")).find(
      b => b.textContent.trim() === "Confirm"
    );
    if (confirmBtn) {
      const origClick = confirmBtn.onclick;
      confirmBtn.addEventListener("click", () => {
        inputs.forEach(input => {
          const ph = (input.placeholder || "").toLowerCase();
          if (ph.includes("qlikcloud") || ph.includes("url") || ph.includes("server") || ph.includes("localhost")) {
            if (input.value) localStorage.setItem("qlik_mcp_url", input.value);
          }
          if (ph.includes("qlik cloud") || ph.includes("name") || ph.includes("stripe")) {
            if (input.value) localStorage.setItem("qlik_mcp_name", input.value);
          }
        });
      });
    }

    // Select SSE in the type dropdown if it's not already
    const selects = dialog.querySelectorAll("button[role='combobox'], select");
    selects.forEach(sel => {
      if (sel.textContent && sel.textContent.includes("stdio")) {
        sel.click();
        setTimeout(() => {
          const sseOption = document.querySelector('[data-value="sse"], [role="option"]');
          if (sseOption && sseOption.textContent.includes("sse")) sseOption.click();
        }, 100);
      }
    });
  }

  // Watch for dialog
  new MutationObserver(mutations => {
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (node.nodeType !== 1) continue;
        const dlg = node.getAttribute?.("role") === "dialog" ? node : node.querySelector?.('[role="dialog"]');
        if (dlg) {
          setTimeout(() => customizeDialog(dlg), 100);
          setTimeout(() => customizeDialog(dlg), 300);
          setTimeout(() => customizeDialog(dlg), 600);
        }
      }
    }
  }).observe(document.body, { childList: true, subtree: true });
})();
