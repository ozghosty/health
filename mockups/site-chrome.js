// Renders the header (wordmark, nav, theme toggle, "Make your own" button) and footer
// from HEALTH.site, so pages carry no personal or site-specific values.
(function () {
  const S = window.HEALTH.site;
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const ghMark = `<svg class="gh" viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>`;

  const copyHref = S.copyButton === "fork" ? `https://github.com/${S.repo}/fork`
    : S.copyButton === "template" ? `https://github.com/${S.repo}/generate` : null;

  const header = document.querySelector(".site-header");
  header.innerHTML = `<div class="container header-inner">
    <a class="wordmark ${S.brandStyle === "sans" ? "sans" : ""}" href="${esc(S.homeUrl)}">${esc(S.brand)}</a>
    <nav class="nav">${(S.nav || []).map((n) => `<a href="${esc(n.url || "./")}"${n.active ? ' class="active" aria-current="page"' : ""}>${esc(n.title)}</a>`).join("")}</nav>
    <div class="header-actions">
      <button class="icon-btn" type="button" data-theme-toggle aria-label="Toggle light and dark theme">◐</button>
      ${copyHref ? `<a class="copy-btn" href="${copyHref}" title="Set up your own copy of this dashboard on GitHub">${ghMark}<span>${esc(S.copyButtonLabel)}</span></a>` : ""}
    </div></div>`;

  const footer = document.querySelector(".site-footer");
  if (footer) footer.innerHTML = `<div class="container">
    <span>${esc(S.footerNote)}</span>
    <span><a href="https://github.com/${S.repo}">${ghMark} ${esc(S.repo)}</a> · Withings + Fitbit · built by GitHub Actions</span></div>`;

  // Same localStorage key as the blog, so the theme choice carries across the site
  header.querySelector("[data-theme-toggle]").addEventListener("click", () => {
    const dark = getComputedStyle(document.documentElement).colorScheme === "dark";
    document.documentElement.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", document.documentElement.dataset.theme); } catch (e) {}
    document.dispatchEvent(new Event("themechange"));
  });
})();
