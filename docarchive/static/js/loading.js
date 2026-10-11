// Loading feedback: a progress bar while a page loads, and a busy state on the button you pressed.
(function () {
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---- top progress bar -------------------------------------------------
  var bar = document.createElement("div");
  bar.className = "route-progress";
  bar.setAttribute("aria-hidden", "true");
  (document.body || document.documentElement).appendChild(bar);

  var width = 0, timer = null;

  function start() {
    if (bar.classList.contains("is-active")) return;
    width = 0;
    bar.classList.add("is-active");
    bar.style.width = "0%";
    // Creep towards 90%: the real load finishes it.
    timer = setInterval(function () {
      width += Math.max((90 - width) * 0.08, 0.4);
      bar.style.width = Math.min(width, 90) + "%";
    }, reduced ? 400 : 120);
  }

  function done() {
    clearInterval(timer);
    bar.style.width = "100%";
    setTimeout(function () {
      bar.classList.remove("is-active");
      bar.style.width = "0%";
    }, 220);
  }

  // ---- button busy state ------------------------------------------------
  function markBusy(button) {
    if (!button || button.classList.contains("is-busy")) return;
    button.classList.add("is-busy");
    button.setAttribute("aria-busy", "true");
    // Left enabled on purpose: a disabled button's name/value never reaches the server.
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement) || form.hasAttribute("data-no-loading")) return;
    if (event.defaultPrevented) return;
    markBusy(event.submitter || form.querySelector("button[type=submit], button:not([type])"));
    start();
  }, true);

  document.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest("a[href]");
    if (!link) return;
    var href = link.getAttribute("href");
    if (!href || href.charAt(0) === "#" || link.target === "_blank" ||
        link.hasAttribute("download") || link.protocol === "mailto:" ||
        event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    if (link.origin && link.origin !== window.location.origin) return;
    start();
  });

  window.addEventListener("pageshow", done);
  window.addEventListener("beforeunload", start);
  if (document.readyState === "complete") done();
  else window.addEventListener("load", done);
})();
