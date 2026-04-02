/**
 * Replaces Chainlit's MCP dialog with Qlik Cloud OAuth form.
 *
 * Plug icon → Tenant URL + Client ID → Connect → OAuth PKCE redirect.
 * After OAuth, JS calls /auth/qlik/connect to store token.
 * Next chat message triggers MCP connection.
 */
(function () {
  "use strict";

  let defaults = { tenant_url: "", client_id: "" };
  fetch("/auth/qlik/defaults").then(r => r.json()).then(d => { defaults = d; }).catch(() => {});

  function replaceDialog(dialog) {
    // Only target the MCP Servers dialog, NOT the Readme dialog
    // The MCP dialog has "MCP Servers" as the title in an h2, and "Connect an MCP" tab
    const title = dialog.querySelector("h2");
    const isMcpDialog = title && (title.textContent.trim() === "MCP Servers");
    const hasConnectTab = Array.from(dialog.querySelectorAll("button")).some(
      b => b.textContent.trim() === "Connect an MCP" || b.textContent.trim() === "Connect to Qlik"
    );
    if (!isMcpDialog && !hasConnectTab) return;
    if (dialog.querySelector("#qlik-form")) return;

    // Hide ALL existing Chainlit content including tabs
    Array.from(dialog.children).forEach(c => (c.style.display = "none"));
    // Also hide any tab bars that might re-render
    dialog.querySelectorAll('[role="tablist"], [class*="Tabs"]').forEach(t => (t.style.display = "none"));

    const tenantVal = localStorage.getItem("qlik_tenant_url") || defaults.tenant_url || "";
    const clientVal = localStorage.getItem("qlik_client_id") || defaults.client_id || "";

    const form = document.createElement("div");
    form.id = "qlik-form";
    form.style.cssText = "padding:24px;";

    const titleRow = document.createElement("div");
    titleRow.style.cssText = "display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;";
    const h2 = document.createElement("h2");
    h2.textContent = "Connect to Qlik Cloud";
    h2.style.cssText = "font-size:18px;font-weight:700;margin:0;color:#e0e0e0;";
    const xBtn = document.createElement("button");
    xBtn.textContent = "\u00d7";
    xBtn.style.cssText = "background:none;border:none;color:#888;font-size:20px;cursor:pointer;";
    titleRow.appendChild(h2);
    titleRow.appendChild(xBtn);
    form.appendChild(titleRow);

    form.appendChild(makeLabel("Qlik Tenant URL"));
    const urlInput = makeInput("https://your-tenant.us.qlikcloud.com", tenantVal);
    form.appendChild(urlInput);

    form.appendChild(makeLabel("OAuth Client ID"));
    const cidInput = makeInput("Client ID from your Qlik tenant admin", clientVal);
    form.appendChild(cidInput);

    const info = document.createElement("div");
    info.style.cssText = "font-size:12px;color:#888;margin-bottom:8px;line-height:1.5;";
    info.innerHTML = 'MCP endpoint: <code style="color:#009845;">&lt;tenant&gt;/api/ai/mcp</code><br/>' +
      'Transport: <code style="color:#009845;">streamable-http</code> with OAuth PKCE';
    form.appendChild(info);

    const help = document.createElement("a");
    help.href = "https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Connecting-Qlik-MCP-server.htm";
    help.target = "_blank";
    help.textContent = "Qlik MCP setup guide";
    help.style.cssText = "display:block;font-size:12px;color:#006580;margin-bottom:20px;text-decoration:none;";
    form.appendChild(help);

    const btnRow = document.createElement("div");
    btnRow.style.cssText = "display:flex;justify-content:flex-end;gap:10px;";
    const cancelBtn = makeButton("Cancel", false);
    const connectBtn = makeButton("Connect", true);
    btnRow.appendChild(cancelBtn);
    btnRow.appendChild(connectBtn);
    form.appendChild(btnRow);

    dialog.appendChild(form);

    const closeDialog = () => {
      dialog.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      const overlay = dialog.closest("[data-state]") || dialog.parentElement;
      if (overlay) overlay.style.display = "none";
    };
    xBtn.onclick = closeDialog;
    cancelBtn.onclick = closeDialog;

    connectBtn.onclick = () => {
      const url = urlInput.value.trim();
      const cid = cidInput.value.trim();
      if (!url || !cid) {
        urlInput.style.borderColor = url ? "#333" : "#d32f2f";
        cidInput.style.borderColor = cid ? "#333" : "#d32f2f";
        return;
      }

      localStorage.setItem("qlik_tenant_url", url);
      localStorage.setItem("qlik_client_id", cid);

      const state = crypto.randomUUID();
      const params = new URLSearchParams({ tenant_url: url, client_id: cid, state: state });
      window.open("/auth/qlik/start?" + params.toString(), "_blank");

      connectBtn.textContent = "Waiting for Qlik approval...";
      connectBtn.disabled = true;
      connectBtn.style.background = "#54565A";
      connectBtn.style.cursor = "wait";

      pollForCompletion(state, closeDialog);
    };

    setTimeout(() => {
      if (!urlInput.value) urlInput.focus();
      else if (!cidInput.value) cidInput.focus();
    }, 150);
  }

  async function pollForCompletion(state, closeDialog) {
    for (let i = 0; i < 90; i++) {
      await new Promise(r => setTimeout(r, 2000));
      try {
        const resp = await fetch("/auth/qlik/status?state=" + encodeURIComponent(state));
        const data = await resp.json();
        if (data.complete && data.access_token) {
          await fetch("/auth/qlik/connect", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              access_token: data.access_token,
              tenant_url: data.tenant_url,
              client_id: data.client_id,
              session_id: "default",
            }),
          });
          closeDialog();
          return;
        }
      } catch (e) {}
    }
  }

  function makeLabel(text) {
    const l = document.createElement("label");
    l.textContent = text;
    l.style.cssText = "display:block;font-size:14px;font-weight:600;margin-bottom:6px;color:#e0e0e0;";
    return l;
  }

  function makeInput(placeholder, value) {
    const i = document.createElement("input");
    i.type = "text";
    i.placeholder = placeholder;
    i.value = value || "";
    i.style.cssText = "width:100%;padding:10px 12px;border-radius:6px;border:1px solid #333;background:#1a2632;color:#e0e0e0;font-size:14px;margin-bottom:16px;box-sizing:border-box;outline:none;";
    i.addEventListener("keydown", e => e.stopPropagation());
    i.addEventListener("keyup", e => e.stopPropagation());
    i.addEventListener("keypress", e => e.stopPropagation());
    i.addEventListener("focus", () => i.style.borderColor = "#009845");
    i.addEventListener("blur", () => i.style.borderColor = "#333");
    return i;
  }

  function makeButton(text, primary) {
    const b = document.createElement("button");
    b.textContent = text;
    b.style.cssText = primary
      ? "padding:8px 20px;border-radius:6px;border:none;background:#009845;color:white;font-size:14px;font-weight:600;cursor:pointer;"
      : "padding:8px 20px;border-radius:6px;border:1px solid #444;background:transparent;color:#e0e0e0;font-size:14px;cursor:pointer;";
    return b;
  }

  new MutationObserver(mutations => {
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
