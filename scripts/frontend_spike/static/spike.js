// Spike-only glue: records htmx events so run_spike.py can read them. No UI text.
window.__events = [];
for (const name of ["htmx:responseError", "htmx:sendError", "htmx:beforeSwap", "htmx:afterSwap", "htmx:swapError", "htmx:invalidPath", "htmx:afterRequest"]) {
  document.addEventListener(name, (evt) => {
    const d = evt.detail || {};
    const xhr = d.xhr;
    window.__events.push({
      name,
      status: xhr ? xhr.status : null,
      retryAfter: xhr ? xhr.getResponseHeader("Retry-After") : null,
      body: xhr && name === "htmx:responseError" ? xhr.responseText : null,
      path: d.pathInfo ? d.pathInfo.requestPath : null,
      elt: d.elt ? (d.elt.id || d.elt.className) : null,
    });
  });
}
document.addEventListener("securitypolicyviolation", (evt) => {
  window.__events.push({ name: "csp-violation", directive: evt.violatedDirective });
});
