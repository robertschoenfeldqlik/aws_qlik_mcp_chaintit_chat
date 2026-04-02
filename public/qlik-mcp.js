/**
 * Replaces Chainlit's MCP dialog with a Qlik Cloud connection form.
 *
 * Plug icon → Qlik Tenant URL + OAuth Client ID → Connect → OAuth redirect.
 *
 * The callback URL for your Qlik admin to register is shown at the bottom.
 */
(function () {
  "use strict";

  function replaceDialog(dialog) {
    if (!dialog.textContent.includes("MCP") && !dialog.textContent.includes("Connect an")) return;
    if (dialog.querySelector("#qlik-connect-form")) return;

    // Hide all existing Chainlit dialog content
    Array.from(dialog.children).forEach((c) => (c.style.display = "none"));

    // Get callback URL for display
    const origin = window.location.origin;
    const callbackUrl = origin + "/auth/qlik/callback";

    const form = document.createElement("div");
    form.id = "qlik-connect-form";
    form.style.cssText = "padding:24px;";
    form.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
        <h2 style="font-size:18px;font-weight:700;margin:0;color:#e0e0e0;">Connect to Qlik Cloud</h2>
        <button id="qlik-close" style="background:none;border:none;color:#888;font-size:20px;cursor:pointer;">&times;</button>
      </div>

      <label style="display:block;font-size:14px;font-weight:600;margin-bottom:6px;color:#e0e0e0;">Qlik Tenant URL</label>
      <input id="qlik-url" type="text" placeholder="https://your-tenant.us.qlikcloud.com"
        style="width:100%;padding:10px 12px;border-radius:6px;border:1px solid #333;background:#1a2632;color:#e0e0e0;font-size:14px;margin-bottom:16px;box-sizing:border-box;" />

      <label style="display:block;font-size:14px;font-weight:600;margin-bottom:6px;color:#e0e0e0;">OAuth Client ID</label>
      <input id="qlik-cid" type="text" placeholder="Client ID from your Qlik tenant admin"
        style="width:100%;padding:10px 12px;border-radius:6px;border:1px solid #333;background:#1a2632;color:#e0e0e0;font-size:14px;margin-bottom:8px;box-sizing:border-box;" />

      <a href="https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Connecting-Qlik-MCP-server.htm"
         target="_blank" style="display:block;font-size:12px;color:#006580;margin-bottom:12px;text-decoration:none;">
        Qlik MCP setup guide
      </a>

      <p style="font-size:11px;color:#666;margin-bottom:20px;">
        OAuth callback URL (register in Qlik admin):<br/>
        <code style="color:#009845;font-size:11px;">${callbackUrl}</code>
      </p>

      <div style="display:flex;justify-content:flex-end;gap:10px;">
        <button id="qlik-cancel" style="padding:8px 20px;border-radius:6px;border:1px solid #444;background:transparent;color:#e0e0e0;font-size:14px;cursor:pointer;">Cancel</button>
        <button id="qlik-connect" style="padding:8px 20px;border-radius:6px;border:none;background:#009845;color:white;font-size:14px;font-weight:600;cursor:pointer;">Connect</button>
      </div>
    `;
    dialog.appendChild(form);

    // Close handlers
    const close = () => {
      dialog.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      const overlay = dialog.closest("[data-state]") || dialog.parentElement;
      if (overlay) overlay.style.display = "none";
    };
    form.querySelector("#qlik-close").onclick = close;
    form.querySelector("#qlik-cancel").onclick = close;

    // Connect handler
    form.querySelector("#qlik-connect").onclick = () => {
      const url = form.querySelector("#qlik-url").value.trim();
      const cid = form.querySelector("#qlik-cid").value.trim();
      const urlEl = form.querySelector("#qlik-url");
      const cidEl = form.querySelector("#qlik-cid");

      if (!url || !cid) {
        urlEl.style.borderColor = url ? "#333" : "#d32f2f";
        cidEl.style.borderColor = cid ? "#333" : "#d32f2f";
        return;
      }

      // Generate a state token and store it so app.py can poll for it
      const state = crypto.randomUUID();
      window.__qlik_oauth_state = state;

      // Store state in sessionStorage so the background poller can find it
      sessionStorage.setItem("qlik_oauth_state", state);
      sessionStorage.setItem("qlik_tenant_url", url);
      sessionStorage.setItem("qlik_client_id", cid);

      // Open OAuth flow
      const params = new URLSearchParams({ tenant_url: url, client_id: cid, state: state });
      window.open("/auth/qlik/start?" + params.toString(), "_blank");

      // Show waiting state
      const btn = form.querySelector("#qlik-connect");
      btn.textContent = "Waiting for Qlik approval...";
      btn.disabled = true;
      btn.style.background = "#54565A";
      btn.style.cursor = "wait";

      // Poll for completion and notify the chat
      pollForCompletion(state, url, close);
    };
  }

  async function pollForCompletion(state, tenantUrl, closeDialog) {
    // Poll the server to check if OAuth completed
    for (let i = 0; i < 90; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      try {
        const resp = await fetch("/auth/qlik/status?state=" + encodeURIComponent(state));
        const data = await resp.json();
        if (data.complete) {
          closeDialog();
          // The server-side poller in app.py will pick up the token and connect
          return;
        }
      } catch (e) {
        // ignore, keep polling
      }
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
