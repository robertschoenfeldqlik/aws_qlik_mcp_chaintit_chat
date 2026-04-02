/**
 * Replaces Chainlit's MCP dialog with a Qlik Cloud form.
 * Pre-fills from localStorage/server defaults.
 * On Connect, fills Chainlit's native SSE fields and clicks Confirm.
 */
(function () {
  "use strict";

  let defaults = { tenant_url: "", client_id: "" };

  // Load saved values
  const savedUrl = localStorage.getItem("qlik_tenant_url") || "";
  const savedClientId = localStorage.getItem("qlik_client_id") || "";

  // Fetch server defaults
  fetch("/auth/qlik/defaults").then(r => r.json()).then(d => {
    defaults = d;
  }).catch(() => {});

  function replaceDialog(dialog) {
    if (!dialog.textContent.includes("MCP") && !dialog.textContent.includes("Connect an")) return;
    if (dialog.querySelector("#qlik-connect-form")) return;

    // Hide all existing Chainlit content
    Array.from(dialog.children).forEach(c => (c.style.display = "none"));

    const tenantVal = savedUrl || defaults.tenant_url || "";
    const clientVal = savedClientId || defaults.client_id || "";

    const form = document.createElement("div");
    form.id = "qlik-connect-form";
    form.style.cssText = "padding:24px;";

    // Title
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
    form.appendChild(makeLabel("Qlik Tenant URL"));
    const urlInput = makeInput("qlik-url", "https://your-tenant.us.qlikcloud.com", tenantVal);
    form.appendChild(urlInput);

    // Client ID
    form.appendChild(makeLabel("OAuth Client ID"));
    const cidInput = makeInput("qlik-cid", "Client ID from your Qlik tenant admin", clientVal);
    form.appendChild(cidInput);

    // Help link
    const help = document.createElement("a");
    help.href = "https://help.qlik.com/en-US/cloud-services/Subsystems/Hub/Content/Sense_Hub/QlikMCP/Connecting-Qlik-MCP-server.htm";
    help.target = "_blank";
    help.textContent = "Qlik MCP setup guide";
    help.style.cssText = "display:block;font-size:12px;color:#006580;margin-bottom:20px;text-decoration:none;";
    form.appendChild(help);

    // Buttons
    const btnRow = document.createElement("div");
    btnRow.style.cssText = "display:flex;justify-content:flex-end;gap:10px;";
    const cancelBtn = makeButton("Cancel", false);
    const connectBtn = makeButton("Connect", true);
    btnRow.appendChild(cancelBtn);
    btnRow.appendChild(connectBtn);
    form.appendChild(btnRow);

    dialog.appendChild(form);

    // Close
    const closeDialog = () => {
      dialog.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      const overlay = dialog.closest("[data-state]") || dialog.parentElement;
      if (overlay) overlay.style.display = "none";
    };
    closeBtn.onclick = closeDialog;
    cancelBtn.onclick = closeDialog;

    // Connect — fill Chainlit's hidden native form and click its Confirm
    connectBtn.onclick = () => {
      const url = urlInput.value.trim();
      const cid = cidInput.value.trim();

      if (!url || !cid) {
        urlInput.style.borderColor = url ? "#333" : "#d32f2f";
        cidInput.style.borderColor = cid ? "#333" : "#d32f2f";
        return;
      }

      // Save to localStorage
      localStorage.setItem("qlik_tenant_url", url);
      localStorage.setItem("qlik_client_id", cid);

      // Build MCP URL
      const mcpUrl = url.replace(/\/$/, "") + "/api/ai/mcp";

      // Find Chainlit's hidden native form inputs and fill them
      const hiddenChildren = Array.from(dialog.children).filter(c => c.style.display === "none");
      let nameInput, urlField, headerField, typeSelect;

      hiddenChildren.forEach(container => {
        container.querySelectorAll("input").forEach(inp => {
          const ph = (inp.placeholder || "").toLowerCase();
          if (ph.includes("stripe") || ph.includes("name")) nameInput = inp;
          if (ph.includes("localhost") || ph.includes("url") || ph.includes("server")) urlField = inp;
          if (ph.includes("authorization") || ph.includes("header") || ph.includes("bearer")) headerField = inp;
        });
      });

      // Fill the native fields
      if (nameInput) setReactValue(nameInput, "Qlik Cloud");
      if (urlField) setReactValue(urlField, mcpUrl);

      // Set headers with client_id for OAuth
      // Qlik MCP uses the client_id in the OAuth flow, pass it as a header hint
      if (headerField) setReactValue(headerField, JSON.stringify({"X-Qlik-OAuth-Client-Id": cid}));

      // Select SSE type if needed
      hiddenChildren.forEach(container => {
        const typeBtn = container.querySelector("button[role='combobox']");
        if (typeBtn && !typeBtn.textContent.includes("sse")) {
          typeBtn.click();
          setTimeout(() => {
            document.querySelectorAll("[role='option']").forEach(opt => {
              if (opt.textContent.trim() === "sse") opt.click();
            });
          }, 100);
        }
      });

      // Show the native form briefly and click Confirm
      setTimeout(() => {
        // Find and click Chainlit's Confirm button
        hiddenChildren.forEach(container => {
          container.querySelectorAll("button").forEach(btn => {
            if (btn.textContent.trim() === "Confirm") {
              container.style.display = "";
              btn.click();
            }
          });
        });

        // Update our button state
        connectBtn.textContent = "Connecting...";
        connectBtn.disabled = true;
        connectBtn.style.background = "#54565A";

        // Close our form after a brief delay
        setTimeout(closeDialog, 500);
      }, 300);
    };

    setTimeout(() => {
      if (!urlInput.value) urlInput.focus();
      else if (!cidInput.value) cidInput.focus();
    }, 150);
  }

  function makeLabel(text) {
    const label = document.createElement("label");
    label.textContent = text;
    label.style.cssText = "display:block;font-size:14px;font-weight:600;margin-bottom:6px;color:#e0e0e0;";
    return label;
  }

  function makeInput(id, placeholder, value) {
    const input = document.createElement("input");
    input.type = "text";
    input.id = id;
    input.placeholder = placeholder;
    input.value = value || "";
    input.style.cssText = "width:100%;padding:10px 12px;border-radius:6px;border:1px solid #333;background:#1a2632;color:#e0e0e0;font-size:14px;margin-bottom:16px;box-sizing:border-box;outline:none;";
    input.addEventListener("keydown", e => e.stopPropagation());
    input.addEventListener("keyup", e => e.stopPropagation());
    input.addEventListener("keypress", e => e.stopPropagation());
    input.addEventListener("focus", () => input.style.borderColor = "#009845");
    input.addEventListener("blur", () => input.style.borderColor = "#333");
    return input;
  }

  function makeButton(text, primary) {
    const btn = document.createElement("button");
    btn.textContent = text;
    btn.style.cssText = primary
      ? "padding:8px 20px;border-radius:6px;border:none;background:#009845;color:white;font-size:14px;font-weight:600;cursor:pointer;"
      : "padding:8px 20px;border-radius:6px;border:1px solid #444;background:transparent;color:#e0e0e0;font-size:14px;cursor:pointer;";
    return btn;
  }

  function setReactValue(input, value) {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // Watch for dialog
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
