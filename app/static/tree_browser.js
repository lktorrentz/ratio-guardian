// Tree browser per la selezione di cartelle scoped-per-disco (vedi
// app/api/disks.py: /browse, /mkdir). Vanilla JS, nessuna dipendenza —
// vedi CLAUDE.md, "niente build frontend pesante".
//
// Uso:
// <div class="tree-browser" data-disk-id="3" data-target="my_input_id" data-initial-path="media/tv"></div>
// <input type="hidden" id="my_input_id" name="relative_path">
(function () {
    function el(tag, className, text) {
        const e = document.createElement(tag);
        if (className) e.className = className;
        if (text !== undefined) e.textContent = text;
        return e;
    }

    function initBrowser(container) {
        const diskId = container.dataset.diskId;
        const targetInput = document.getElementById(container.dataset.target);
        let currentPath = container.dataset.initialPath || "";

        function render() {
            container.innerHTML = "";
            const header = el("div", "flex items-center justify-between mb-2");
            const label = el("span", "font-mono text-xs text-gray-600", "/" + currentPath);
            header.appendChild(label);

            const useBtn = el("button", "text-xs bg-emerald-600 text-white px-2 py-1 rounded hover:bg-emerald-700", "Usa questa cartella");
            useBtn.type = "button";
            useBtn.onclick = function () {
                targetInput.value = currentPath;
                const confirmEl = container.querySelector(".tree-browser-selected");
                if (confirmEl) confirmEl.textContent = "Selezionato: /" + currentPath;
            };
            header.appendChild(useBtn);
            container.appendChild(header);

            const list = el("div", "border rounded divide-y max-h-56 overflow-y-auto bg-white");
            if (currentPath) {
                const up = el("button", "w-full text-left px-3 py-1.5 text-sm hover:bg-gray-50 text-gray-500", "⬆ ..");
                up.type = "button";
                up.onclick = function () {
                    const parts = currentPath.split("/");
                    parts.pop();
                    currentPath = parts.join("/");
                    load();
                };
                list.appendChild(up);
            }
            container.appendChild(list);

            const actions = el("div", "flex items-center gap-2 mt-2");
            const mkdirBtn = el("button", "text-xs text-gray-600 hover:text-gray-900 underline", "Crea cartella qui");
            mkdirBtn.type = "button";
            mkdirBtn.onclick = function () {
                const name = prompt("Nome della nuova cartella:");
                if (!name) return;
                fetch(`/api/disks/${diskId}/mkdir`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ path: currentPath ? currentPath + "/" + name : name }),
                })
                    .then(function (r) {
                        if (!r.ok) return r.json().then(function (b) { throw new Error(b.detail || r.statusText); });
                        return load();
                    })
                    .catch(function (err) { alert("Errore creazione cartella: " + err.message); });
            };
            actions.appendChild(mkdirBtn);
            container.appendChild(actions);

            const selected = el("div", "tree-browser-selected text-xs text-gray-500 mt-1", "");
            container.appendChild(selected);

            return list;
        }

        function load() {
            const list = render();
            fetch(`/api/disks/${diskId}/browse?path=${encodeURIComponent(currentPath)}`)
                .then(function (r) {
                    if (!r.ok) return r.json().then(function (b) { throw new Error(b.detail || r.statusText); });
                    return r.json();
                })
                .then(function (data) {
                    const dirs = data.entries.filter(function (e) { return e.is_dir; });
                    if (dirs.length === 0) {
                        list.appendChild(el("div", "px-3 py-2 text-xs text-gray-400 italic", "Nessuna sottocartella"));
                    }
                    dirs.forEach(function (entry) {
                        const btn = el("button", "w-full text-left px-3 py-1.5 text-sm hover:bg-gray-50", "📁 " + entry.name);
                        btn.type = "button";
                        btn.onclick = function () {
                            currentPath = currentPath ? currentPath + "/" + entry.name : entry.name;
                            load();
                        };
                        list.appendChild(btn);
                    });
                })
                .catch(function (err) {
                    list.appendChild(el("div", "px-3 py-2 text-xs text-red-600", "Errore: " + err.message));
                });
        }

        load();
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll(".tree-browser").forEach(initBrowser);
    });
})();
