/**
 * Replaces Chainlit's generic MCP dialog with a simple Qlik Cloud form.
 *
 * Plug icon → "Connect to Qlik Cloud" with just:
 *   - Qlik Tenant URL
 *   - OAuth Client ID
 *   - Connect button → OAuth redirect
 */
(function () {
  "use strict";

  // Grab Chainlit session ID from the WebSocket URL
  function getSessionId() {
    if (window.__chainlit_session_id) return window.__chainlit_session_id;
    try {
      // Chainlit stores session info in sessionStorage
      for (let i = 0; i < sessionStorage.length; i++) {
        const key = sessionStorage.key(i);
        const val = sessionStorage.getItem(key);
        if (val && val.length > 20 && val.length < 80) {
          window.__chainlit_session_id = val;
          return val;
        }
      }
    } catch (e) {}
    return "";
  }

  function replaceDialog(dialog) {
    // Only target MCP dialog
    if (!dialog.textContent.includes("MCP") && !dialog.textContent.includes("Connect an")) return;
    // Don't replace if we already did
    if (dialog.querySelector("#qlik-connect-form")) return;

    // Hide ALL existing content inside the dialog
    const children = Array.from(dialog.children);
    children.forEach((child) => {
      child.style.display = "none";
    });

    // Create our Qlik form
    const form = document.createElement("div");
    form.id = "qlik-connect-form";
    form.style.cssText = "padding:24px;";

    form.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">
        <h2 style="font-size:18px;font-weight:700;margin:0;color:#e0e0e0;">Connect to Qlik Cloud</h2>
        <button id="qlik-close" style="background:none;border:none;color:#888;font-size:20px;cursor:pointer;padding:4px 8px;">&times;</button>
      </div>

      <label style="display:block;font-size:14px;font-weight:600;margin-bottom:6px;color:#e0e0e0;">Qlik Tenant URL</label>
      <input id="qlik-url" type="text" placeholder="https://your-tenant.us.qlikcloud.com"
        style="width:100%;padding:10px 12px;border-radius:6px;border:1px solid #333;background:#1a2632;color:#e0e0e0;font-size:14px;margin-bottom:16px;box-sizing:border-box;outline:none;" />

      <label style="display:block;font-size:14px;font-weight:600;margin-bottom:6px;color:#e0e0e0;">OAuth Client ID</label>
      <input id="qlik-cid" type="text" placeholder="Client ID from your Qlik tenant admin"
        style="width:100%;padding:10px 12px;border-radius:6px;border:1px solid #333;background:#1a2632;color:#e0e0e0;font-size:14px;margin-bottom:8px;box-sizing:border-box;outline:none;" />

      <a href="https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Connecting-Qlik-MCP-server.htm"
         target="_blank" style="display:block;font-size:12px;color:#006580;margin-bottom:20px;text-decoration:none;">
        Qlik MCP setup guide
      </a>

      <div style="display:flex;justify-content:flex-end;gap:10px;">
        <button id="qlik-cancel" style="padding:8px 20px;border-radius:6px;border:1px solid #444;background:transparent;color:#e0e0e0;font-size:14px;cursor:pointer;">Cancel</button>
        <button id="qlik-connect" style="padding:8px 20px;border-radius:6px;border:none;background:#009845;color:white;font-size:14px;font-weight:600;cursor:pointer;">Connect</button>
      </div>
    `;

    dialog.appendChild(form);

    // Focus styling
    const urlInput = form.querySelector("#qlik-url");
    const cidInput = form.querySelector("#qlik-cid");
    [urlInput, cidInput].forEach((inp) => {
      inp.addEventListener("focus", () => inp.style.borderColor = "#009845");
      inp.addEventListener("blur", () => inp.style.borderColor = "#333");
    });

    // Close/Cancel
    const closeDialog = () => {
      dialog.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      // Fallback: just hide the dialog overlay
      const overlay = dialog.closest("[data-state]") || dialog.parentElement;
      if (overlay) overlay.style.display = "none";
    };
    form.querySelector("#qlik-close").onclick = closeDialog;
    form.querySelector("#qlik-cancel").onclick = closeDialog;

    // Connect button
    const connectBtn = form.querySelector("#qlik-connect");
    connectBtn.onclick = () => {
      const url = urlInput.value.trim();
      const cid = cidInput.value.trim();

      if (!url || !cid) {
        urlInput.style.borderColor = url ? "#333" : "#d32f2f";
        cidInput.style.borderColor = cid ? "#333" : "#d32f2f";
        return;
      }

      const params = new URLSearchParams({
        session_id: getSessionId(),
        tenant_url: url,
        client_id: cid,
      });
      window.open("/auth/qlik/start?" + params.toString(), "_blank");

      connectBtn.textContent = "Waiting for approval...";
      connectBtn.disabled = true;
      connectBtn.style.background = "#54565A";
      connectBtn.style.cursor = "wait";
    };
  }

  // Watch for dialog
  new MutationObserver((mutations) => {
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (node.nodeType !== 1) continue;
        const dlg = node.getAttribute?.("role") === "dialog"
          ? node : node.querySelector?.('[role="dialog"]');
        if (dlg) {
          setTimeout(() => replaceDialog(dlg), 80);
          setTimeout(() => replaceDialog(dlg), 250);
        }
      }
    }
  }).observe(document.body, { childList: true, subtree: true });
})();
