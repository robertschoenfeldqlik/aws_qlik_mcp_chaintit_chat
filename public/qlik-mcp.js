/**
 * Replaces Chainlit's MCP dialog with a Qlik Cloud connection form.
 * Plug icon → Qlik Tenant URL + OAuth Client ID → Connect → OAuth redirect.
 */
(function () {
  "use strict";

  function replaceDialog(dialog) {
    if (!dialog.textContent.includes("MCP") && !dialog.textContent.includes("Connect an")) return;
    if (dialog.querySelector("#qlik-connect-form")) return;

    // Hide all existing Chainlit dialog content
    Array.from(dialog.children).forEach((c) => (c.style.display = "none"));

    const origin = window.location.origin;
    const callbackUrl = origin + "/auth/qlik/callback";

    const form = document.createElement("div");
    form.id = "qlik-connect-form";
    form.style.cssText = "padding:24px;";

    // Build form with createElement (not innerHTML) for proper event handling
    // Title row
    const titleRow = document.createElement("div");
    titleRow.style.cssText = "display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;";
    const h2 = document.createElement("h2");
    h2.textContent = "Connect to Qlik Cloud";
    h2.style.cssText = "font-size:18px;font-weight:700;margin:0;color:#e0e0e0;";
    const closeBtn = document.createElement("button");
    closeBtn.textContent = "\u00d7";
    closeBtn.style.cssText = "background:none;border:none;color:#888;font-size:20px;cursor:pointer;";
    titleRow.appendChild(h2);
    titleRow.appendChild(closeBtn);
    form.appendChild(titleRow);

    // Tenant URL
    const urlLabel = document.createElement("label");
    urlLabel.textContent = "Qlik Tenant URL";
    urlLabel.style.cssText = "display:block;font-size:14px;font-weight:600;margin-bottom:6px;color:#e0e0e0;";
    form.appendChild(urlLabel);

    const urlInput = document.createElement("input");
    urlInput.type = "text";
    urlInput.id = "qlik-url";
    urlInput.placeholder = "https://your-tenant.us.qlikcloud.com";
    urlInput.style.cssText = "width:100%;padding:10px 12px;border-radius:6px;border:1px solid #333;background:#1a2632;color:#e0e0e0;font-size:14px;margin-bottom:16px;box-sizing:border-box;outline:none;";
    // CRITICAL: Stop event propagation so Chainlit doesn't intercept keystrokes
    urlInput.addEventListener("keydown", (e) => e.stopPropagation());
    urlInput.addEventListener("keyup", (e) => e.stopPropagation());
    urlInput.addEventListener("keypress", (e) => e.stopPropagation());
    urlInput.addEventListener("focus", () => urlInput.style.borderColor = "#009845");
    urlInput.addEventListener("blur", () => urlInput.style.borderColor = "#333");
    form.appendChild(urlInput);

    // Client ID
    const cidLabel = document.createElement("label");
    cidLabel.textContent = "OAuth Client ID";
    cidLabel.style.cssText = "display:block;font-size:14px;font-weight:600;margin-bottom:6px;color:#e0e0e0;";
    form.appendChild(cidLabel);

    const cidInput = document.createElement("input");
    cidInput.type = "text";
    cidInput.id = "qlik-cid";
    cidInput.placeholder = "Client ID from your Qlik tenant admin";
    cidInput.style.cssText = "width:100%;padding:10px 12px;border-radius:6px;border:1px solid #333;background:#1a2632;color:#e0e0e0;font-size:14px;margin-bottom:8px;box-sizing:border-box;outline:none;";
    cidInput.addEventListener("keydown", (e) => e.stopPropagation());
    cidInput.addEventListener("keyup", (e) => e.stopPropagation());
    cidInput.addEventListener("keypress", (e) => e.stopPropagation());
    cidInput.addEventListener("focus", () => cidInput.style.borderColor = "#009845");
    cidInput.addEventListener("blur", () => cidInput.style.borderColor = "#333");
    form.appendChild(cidInput);

    // Help link
    const help = document.createElement("a");
    help.href = "https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Connecting-Qlik-MCP-server.htm";
    help.target = "_blank";
    help.textContent = "Qlik MCP setup guide";
    help.style.cssText = "display:block;font-size:12px;color:#006580;margin-bottom:12px;text-decoration:none;";
    form.appendChild(help);

    // Callback URL info
    const info = document.createElement("p");
    info.style.cssText = "font-size:11px;color:#666;margin-bottom:20px;";
    info.innerHTML = 'OAuth callback URL (register in Qlik admin):<br/><code style="color:#009845;font-size:11px;">' + callbackUrl + '</code>';
    form.appendChild(info);

    // Buttons
    const btnRow = document.createElement("div");
    btnRow.style.cssText = "display:flex;justify-content:flex-end;gap:10px;";

    const cancelBtn = document.createElement("button");
    cancelBtn.textContent = "Cancel";
    cancelBtn.style.cssText = "padding:8px 20px;border-radius:6px;border:1px solid #444;background:transparent;color:#e0e0e0;font-size:14px;cursor:pointer;";

    const connectBtn = document.createElement("button");
    connectBtn.textContent = "Connect";
    connectBtn.style.cssText = "padding:8px 20px;border-radius:6px;border:none;background:#009845;color:white;font-size:14px;font-weight:600;cursor:pointer;";

    btnRow.appendChild(cancelBtn);
    btnRow.appendChild(connectBtn);
    form.appendChild(btnRow);

    dialog.appendChild(form);

    // Close handler
    const closeDialog = () => {
      dialog.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      const overlay = dialog.closest("[data-state]") || dialog.parentElement;
      if (overlay) overlay.style.display = "none";
    };
    closeBtn.onclick = closeDialog;
    cancelBtn.onclick = closeDialog;

    // Connect handler
    connectBtn.onclick = () => {
      const url = urlInput.value.trim();
      const cid = cidInput.value.trim();

      if (!url || !cid) {
        urlInput.style.borderColor = url ? "#333" : "#d32f2f";
        cidInput.style.borderColor = cid ? "#333" : "#d32f2f";
        return;
      }

      const state = crypto.randomUUID();

      // Open OAuth flow
      const params = new URLSearchParams({ tenant_url: url, client_id: cid, state: state });
      window.open("/auth/qlik/start?" + params.toString(), "_blank");

      // Show waiting state
      connectBtn.textContent = "Waiting for Qlik approval...";
      connectBtn.disabled = true;
      connectBtn.style.background = "#54565A";
      connectBtn.style.cursor = "wait";

      // Poll for completion
      pollForCompletion(state, closeDialog);
    };

    // Focus the first input
    setTimeout(() => urlInput.focus(), 100);
  }

  async function pollForCompletion(state, closeDialog) {
    for (let i = 0; i < 90; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      try {
        const resp = await fetch("/auth/qlik/status?state=" + encodeURIComponent(state));
        const data = await resp.json();
        if (data.complete) {
          closeDialog();
          return;
        }
      } catch (e) {}
    }
  }

  // Watch for dialog
  new MutationObserver((mutations) => {
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (node.nodeType !== 1) continue;
        const dlg = node.getAttribute?.("role") === "dialog" ? node : node.querySelector?.('[role="dialog"]');
        if (dlg) {
          setTimeout(() => replaceDialog(dlg), 80);
          setTimeout(() => replaceDialog(dlg), 250);
        }
      }
    }
  }).observe(document.body, { childList: true, subtree: true });
})();
