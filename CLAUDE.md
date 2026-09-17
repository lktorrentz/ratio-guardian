# Ratio Guardian — guida per la sessione Claude Code

## Cos'è

Tool (web app + worker in background, distribuito come container Docker) che risolve il problema del "seeding perso": file scaricati da un tracker privato (UNIT3D-based, es. ITT) che l'utente ha spostato/rinominato fuori dalla cartella gestita dal client torrent, rompendo il seeding senza rendersene conto.

Il tool scansiona le librerie media dell'utente, prova a far combaciare ogni file con un torrent disponibile sul tracker (via storico personale quando disponibile, altrimenti via ricerca sul catalogo + verifica dimensione/hash mediainfo), crea un hardlink nel path corretto e rimette il file in seeding sul client torrent con recheck forzato.

**Non è specifico per Unraid né per arr-stack.** Deve girare bene anche con dischi separati senza FUSE/RAID, e senza Sonarr/Radarr — quelle integrazioni sono adapter opzionali, non dipendenze.

Leggi **`docs/SPEC.md`** prima di scrivere codice: contiene tutte le decisioni di design già prese in fase di progettazione (schema disco/media path, matching engine, confidence/review queue, flusso hardlink+seed). Non redecidere quelle cose da zero — se qualcosa in SPEC.md sembra sbagliato o incompleto, fermati e chiedi prima di deviare.

## Stack tecnico (confermato con l'utente il 2026-09-17)

- **Python 3.12**, FastAPI per API + web UI (Jinja2 + HTMX, niente build frontend pesante)
- **SQLite** via SQLAlchemy (schema in `docs/schema.sql`) — sufficiente per questo carico, niente Postgres
- **APScheduler** in-process per lo scheduling (cron-like, configurabile da UI, niente cron esterno di sistema)
- **httpx** per le chiamate al tracker (async-friendly)
- **qbittorrent-api** (libreria pip) per il client torrent, primo adapter supportato
- **pymediainfo** (o subprocess su `mediainfo` CLI, da valutare quale sia più affidabile in container) per calcolare l'Unique ID
- **Container singolo con supervisord**: web (FastAPI/uvicorn) e worker/scheduler nello stesso container, gestiti da supervisord — vedi `Dockerfile` e `docker/supervisord.conf`.

## Convenzioni

- **Gli adapter sono contratti, non implementazioni fisse.** In `app/adapters/*/base.py` trovi le interfacce astratte già decise (tracker, media resolver, torrent client). Il primo adapter concreto da implementare per ciascuno:
  - tracker → UNIT3D (vedi `docs/SPEC.md` sezione Tracker Adapter per i dettagli API verificati e i limiti noti)
  - media resolver → filename parser (fallback universale); Sonarr/Radarr come adapter aggiuntivo, non come prima implementazione
  - torrent client → qBittorrent (già in uso lato utente)
- **Ogni azione distruttiva o irreversibile passa dalla `match_review` queue se la confidence non è massima.** Non bypassare mai questo meccanismo per "velocizzare" — vedi SPEC.md sezione Confidence.
- **Mai `skip_checking` sul client torrent.** Il recheck reale è un requisito funzionale, non un dettaglio implementativo opzionale.
- **Path traversal:** qualunque endpoint che tocca il filesystem (browse, mkdir, hardlink) deve passare dalla funzione di scoping condivisa descritta in `docs/SPEC.md` (sezione File Browser API). Non duplicare quella logica in più punti.
- **Configurazione:** `config.example.yaml` (statico, richiede restart) contiene solo `disk_scan_root` (radice dei bind mount dei dischi fisici, default `/mnt`) e `data_dir`. I dischi fisici NON sono elencati lì: si scoprono scansionando `disk_scan_root` e si aggiungono dalla Web UI (basta il bind mount Docker sotto quella radice). Tutto il resto (dischi logici, media path, tracker, credenziali, scheduling, soglie) vive nel DB ed è editabile da UI senza restart.

## Struttura repo di partenza

```
ratio-guardian/
  CLAUDE.md                  # questo file
  docker-compose.yml
  config.example.yaml
  .env.example
  requirements.txt
  docs/
    SPEC.md                  # spec funzionale/architetturale completa
    schema.sql                # schema DB
  app/
    adapters/
      tracker/base.py         # interfaccia + stub UNIT3D
      media_resolver/base.py  # interfaccia + stub filename-parser
      torrent_client/base.py  # interfaccia + stub qBittorrent
    # il resto della struttura (models, api routes, worker, web UI)
    # va creato durante la sessione — non è ancora definito nel dettaglio
```

## Roadmap suggerita (per la prima sessione)

1. Conferma stack tecnico con l'utente (vedi sopra) e inizializza il progetto (venv/poetry, struttura cartelle, requirements)
2. Applica `docs/schema.sql`, monta i modelli SQLAlchemy corrispondenti
3. Implementa il file browser API scoped-per-disco (è la base per la UI di configurazione, e il pattern di scoping va riusato ovunque)
4. Implementa l'adapter media resolver filename-based (senza dipendenze esterne) + il flusso di scansione che popola `media_item`
5. Implementa l'adapter tracker UNIT3D (solo ricerca per tmdb_id + fetch dettagli torrent; lo storico/scraping è un secondo step, esplicitamente più fragile — vedi SPEC.md)
6. Motore di matching (size + mediainfo unique id → confidence) e scrittura in `candidate`
7. Coda di revisione in UI (approvazione manuale match "pending")
8. Esecutore hardlink + integrazione qBittorrent (add torrent + recheck forzato)
9. Scheduler configurabile da UI + storico run (`run_log`)

Non è vincolante seguire quest'ordine alla lettera, ma rispetta le dipendenze logiche (es. non ha senso costruire la review queue prima che esista un `candidate` da revisionare).

## Decisioni confermate con l'utente (2026-09-17)

- Nome definitivo del progetto: **Ratio Guardian**
- Stack: Python/FastAPI come proposto sopra
- Web e worker: stesso container, stesso processo supervisord (non due servizi docker-compose separati)
- Soglia di confidence di default per l'auto-approvazione: **0.95** (`app_settings.confidence_threshold_auto`)
- Libreria di parsing filename: **guessit** (usata in `app/adapters/media_resolver/base.py`)

## Cose esplicitamente NON ancora decise (chiedi all'utente, non assumere)

- Se e come implementare davvero lo scraping dello storico UNIT3D (è stato individuato come rischioso/fragile in fase di ricerca — l'endpoint pubblico `/api/user` dà solo statistiche aggregate, non la lista dei torrent; la lista è una pagina HTML autenticata non documentata come API)
