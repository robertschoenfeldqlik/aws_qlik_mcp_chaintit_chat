/**
 * Qlik MCP Dialog Customization
 *
 * Watches for Chainlit's MCP connection dialog to open, then:
 * 1. Changes the title from "MCP Servers" to "Connect to Qlik Cloud"
 * 2. Pre-fills Name with "Qlik Cloud"
 * 3. Selects SSE as the type (since stdio/streamable-http are disabled)
 * 4. Sets placeholder on URL field to show the Qlik MCP pattern
 */
(function () {
  "use strict";

  const QLIK_NAME = "Qlik Cloud";
  const QLIK_URL_PLACEHOLDER =
    "https://your-tenant.us.qlikcloud.com/api/ai/mcp";

  function customizeDialog(dialog) {
    // Change the dialog title
    const title = dialog.querySelector("h2, [class*='DialogTitle']");
    if (title && title.textContent.includes("MCP")) {
      title.textContent = "Connect to Qlik Cloud";
    }

    // Find all input fields
    const inputs = dialog.querySelectorAll("input");

    inputs.forEach((input) => {
      const placeholder = (input.placeholder || "").toLowerCase();

      // Pre-fill Name field
      if (placeholder.includes("example: stripe") || placeholder.includes("name")) {
        if (!input.value) {
          setReactInputValue(input, QLIK_NAME);
          input.placeholder = "Qlik Cloud";
        }
      }

      // Pre-fill Server URL field
      if (
        placeholder.includes("localhost") ||
        placeholder.includes("url") ||
        placeholder.includes("server")
      ) {
        input.placeholder = QLIK_URL_PLACEHOLDER;
      }

      // Customize Headers placeholder
      if (placeholder.includes("authorization") || placeholder.includes("header")) {
        input.placeholder = 'Optional: {"Authorization": "Bearer TOKEN"}';
      }
    });

    // Change "Connect an MCP" tab text
    const tabs = dialog.querySelectorAll("button");
    tabs.forEach((btn) => {
      if (btn.textContent.trim() === "Connect an MCP") {
        btn.textContent = "Connect to Qlik";
      }
    });
  }

  /**
   * Set value on a React-controlled input (React ignores direct .value changes).
   */
  function setReactInputValue(input, value) {
    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value"
    ).set;
    nativeInputValueSetter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // Watch for the MCP dialog to appear
  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      for (const node of mutation.addedNodes) {
        if (node.nodeType !== 1) continue;

        // Check if this is a dialog or contains one
        const dialog =
          node.getAttribute && node.getAttribute("role") === "dialog"
            ? node
            : node.querySelector && node.querySelector('[role="dialog"]');

        if (dialog) {
          // Small delay to let React render the form fields
          setTimeout(() => customizeDialog(dialog), 100);
          setTimeout(() => customizeDialog(dialog), 300);
        }
      }
    }
  });

  observer.observe(document.body, { childList: true, subtree: true });
})();
