document.addEventListener("DOMContentLoaded", () => {
  let tooltip = null;

  function hideTooltip() {
    if (tooltip) {
      tooltip.remove();
      tooltip = null;
    }
  }

  function renderTooltip(target, items, event) {
    hideTooltip();
    tooltip = document.createElement("div");
    tooltip.className = "context-tooltip-popup";

    const title = target.dataset.tooltipTitle || "Details";
    const list = Array.isArray(items) ? items : [];

    const header = document.createElement("div");
    header.className = "context-tooltip-title";
    header.textContent = title;
    tooltip.appendChild(header);

    const body = document.createElement("div");
    body.className = "context-tooltip-body";

    list.forEach((entry) => {
      const row = document.createElement("div");
      row.className = "context-tooltip-row";
      row.textContent = `${entry.label}: ${entry.value}`;
      body.appendChild(row);
    });

    if (list.length === 0) {
      const empty = document.createElement("div");
      empty.className = "context-tooltip-row";
      empty.textContent = "No data available";
      body.appendChild(empty);
    }

    tooltip.appendChild(body);

    document.body.appendChild(tooltip);

    const padding = 12;
    let x = event.pageX + padding;
    let y = event.pageY + padding;
    const { clientWidth, clientHeight } = tooltip;

    if (x + clientWidth > window.scrollX + window.innerWidth) {
      x = event.pageX - clientWidth - padding;
    }
    if (y + clientHeight > window.scrollY + window.innerHeight) {
      y = event.pageY - clientHeight - padding;
    }

    tooltip.style.left = `${x}px`;
    tooltip.style.top = `${y}px`;
  }

  document.addEventListener("contextmenu", (event) => {
    const target = event.target.closest(".context-tooltip");
    if (!target) {
      return;
    }
    event.preventDefault();
    const payload = target.dataset.breakdown;
    if (!payload) {
      return;
    }
    try {
      const parsed = JSON.parse(payload);
      renderTooltip(target, parsed, event);
    } catch (err) {
      console.error("Failed to parse breakdown payload", err);
    }
  });

  document.addEventListener("click", hideTooltip);
  document.addEventListener("keyup", (event) => {
    if (event.key === "Escape") {
      hideTooltip();
    }
  });
});
