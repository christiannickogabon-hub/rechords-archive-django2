/* Collapses the sidebar to an icon rail.
   The menu starts hidden every time the system is opened; opening it lasts for
   that visit only. base.html adds the class before the first paint, so the
   sidebar never flashes wide and then snaps shut. */
(function () {
    "use strict";

    var KEY = "sidebar";
    var root = document.documentElement;

    function isCollapsed() {
        return root.classList.contains("sidebar-collapsed");
    }

    /* With the labels hidden there is nothing to read, so the link text moves
       into a tooltip while the rail is narrow. */
    function syncTooltips() {
        var collapsed = isCollapsed();
        document.querySelectorAll(".sidebar nav a").forEach(function (link) {
            if (collapsed) {
                var label = link.querySelector(".navlabel");
                link.title = label ? label.textContent.trim() : "";
            } else {
                link.removeAttribute("title");
            }
        });
    }

    function syncButton() {
        var collapsed = isCollapsed();
        document.querySelectorAll(".sidebar-toggle").forEach(function (btn) {
            var text = collapsed ? "Show the menu" : "Hide the menu";
            btn.setAttribute("aria-label", text);
            btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
            btn.title = text;
        });
    }

    function apply(collapsed) {
        root.classList.toggle("sidebar-collapsed", collapsed);
        try {
            /* this visit only — a new one starts with the menu closed again */
            sessionStorage.setItem(KEY, collapsed ? "collapsed" : "open");
        } catch (e) {
            /* private window or blocked storage: the toggle still works for this page */
        }
        syncButton();
        syncTooltips();
    }

    document.addEventListener("click", function (e) {
        var btn = e.target.closest(".sidebar-toggle");
        if (!btn) {
            return;
        }
        e.preventDefault();
        apply(!isCollapsed());
    });

    syncButton();
    syncTooltips();
}());
