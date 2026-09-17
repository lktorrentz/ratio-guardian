// Banner di stato live per la run in corso, visibile su tutte le pagine
// (vedi base.html). Polling di GET /api/runs/current — vanilla JS, nessuna
// dipendenza, coerente con tree_browser.js.
(function () {
    function el(tag, className, text) {
        const e = document.createElement(tag);
        if (className) e.className = className;
        if (text !== undefined) e.textContent = text;
        return e;
    }

    function render(banner, run) {
        banner.innerHTML = "";
        if (!run) {
            banner.hidden = true;
            return;
        }
        banner.hidden = false;
        const label = run.items_total
            ? `Run in corso (${run.run_type}): ${run.items_scanned}/${run.items_total} scansionati`
            : `Run in corso (${run.run_type})…`;
        banner.appendChild(el("span", "", label));
        const link = el("a", "underline ml-3", "Storico run");
        link.href = "/runs";
        banner.appendChild(link);
    }

    function poll(banner) {
        fetch("/api/runs/current")
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (run) { render(banner, run); })
            .catch(function () { /* silenzioso: mai rompere la navigazione per un poll fallito */ });
    }

    document.addEventListener("DOMContentLoaded", function () {
        const banner = document.getElementById("run-status-banner");
        if (!banner) return;
        poll(banner);
        setInterval(function () { poll(banner); }, 4000);
    });
})();
