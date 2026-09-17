// Popup di stato live per la run in corso, sempre visibile in basso a
// destra su tutte le pagine (vedi base.html). Polling di GET
// /api/runs/current ogni 3s — vanilla JS, nessuna dipendenza, coerente
// con tree_browser.js.
(function () {
    var PHASES = [
        { key: "scanning", label: "Scansione libreria" },
        { key: "matching", label: "Ricerca sul tracker" },
        { key: "executing", label: "Hardlink + seeding" },
    ];

    function el(tag, className, text) {
        const e = document.createElement(tag);
        if (className) e.className = className;
        if (text !== undefined) e.textContent = text;
        return e;
    }

    function phaseStatus(run, phaseKey) {
        if (run.current_phase === phaseKey) return "current";
        const currentIdx = PHASES.findIndex(function (p) { return p.key === run.current_phase; });
        const thisIdx = PHASES.findIndex(function (p) { return p.key === phaseKey; });
        if (currentIdx === -1) return "pending";
        return thisIdx < currentIdx ? "done" : "pending";
    }

    function render(popup, run) {
        popup.innerHTML = "";
        if (!run) {
            popup.hidden = true;
            return;
        }
        popup.hidden = false;

        const header = el("div", "flex items-center justify-between px-3 py-2 bg-slate-900 text-white rounded-t-lg");
        header.appendChild(el("span", "font-medium", "Run in corso (" + run.run_type + ")"));
        const link = el("a", "text-slate-300 hover:text-white underline", "Storico");
        link.href = "/runs";
        header.appendChild(link);
        popup.appendChild(header);

        const body = el("div", "p-3 space-y-2.5 bg-white rounded-b-lg");
        PHASES.forEach(function (phase) {
            const status = phaseStatus(run, phase.key);
            const row = el("div", "flex items-center gap-2");

            let icon;
            if (status === "done") {
                icon = el(
                    "span",
                    "w-4 h-4 rounded-full bg-emerald-500 text-white flex items-center justify-center text-[10px] flex-shrink-0",
                    "✓"
                );
            } else if (status === "current") {
                icon = el(
                    "span",
                    "w-4 h-4 rounded-full border-2 border-blue-600 border-t-transparent flex-shrink-0 animate-spin"
                );
            } else {
                icon = el("span", "w-4 h-4 rounded-full border border-gray-300 flex-shrink-0");
            }
            row.appendChild(icon);

            const textWrap = el("div", "flex-1");
            textWrap.appendChild(el("div", status === "pending" ? "text-gray-400" : "text-gray-800", phase.label));
            if (status === "current") {
                const detail = run.phase_total
                    ? (run.phase_done || 0) + " / " + run.phase_total
                    : "in corso…";
                textWrap.appendChild(el("div", "text-gray-500", detail));
            }
            row.appendChild(textWrap);

            body.appendChild(row);
        });
        popup.appendChild(body);
    }

    function poll(popup) {
        fetch("/api/runs/current")
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (run) { render(popup, run); })
            .catch(function () { /* silenzioso: mai rompere la navigazione per un poll fallito */ });
    }

    document.addEventListener("DOMContentLoaded", function () {
        const popup = document.getElementById("run-status-popup");
        if (!popup) return;
        poll(popup);
        setInterval(function () { poll(popup); }, 3000);
    });
})();
