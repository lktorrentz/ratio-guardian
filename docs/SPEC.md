# Ratio Guardian — Spec funzionale e architetturale

Documento consolidato dalla sessione di design. Riflette le decisioni prese, non un brainstorming aperto: dove qualcosa è ancora esplicitamente aperto è segnalato come tale.

## 1. Problema

Utenti di tracker privati (UNIT3D-based) scaricano un torrent, lo tengono in seed per un po', poi — spesso per ignoranza del funzionamento hardlink — spostano o rinominano il file, rompendo il seeding senza accorgersene. Il tool deve:

1. Trovare questi file "orfani" nelle librerie media
2. Identificare a quale torrent del tracker corrispondono
3. Ricreare un hardlink nel path corretto con il nome esatto atteso dal tracker
4. Rimettere il torrent in seeding sul client, verificando i dati (recheck reale, mai skip)

Deve funzionare come importer massivo una tantum (libreria già "sporca" da anni) e come job schedulato periodico.

## 2. Requisiti di genericità (vincolanti)

- **Non deve assumere Unraid/FUSE.** Un utente può avere N dischi fisici separati, ciascuno con propria porzione di libreria, senza alcun filesystem unificante.
- **Non deve assumere Sonarr/Radarr.** Integrazione opzionale come adapter aggiuntivo per il media resolver, mai come dipendenza.
- **Un solo adapter tracker al momento: UNIT3D.** Architettura ad adapter per permettere altri tracker software in futuro (es. Gazelle), ma non c'è pressione a generalizzare oltre finché non serve davvero.
- Distribuzione: container Docker, con Web UI per la configurazione (troppi parametri per una CLI/YAML monolitico).

## 3. Modello dischi e librerie media

### Perché questo modello

Un utente senza RAID/FUSE ha dischi fisici distinti, ciascuno potenzialmente con una porzione della libreria (es. `disk1` ha una parte dei film, `disk2` un'altra parte + le serie). L'hardlink funziona solo entro lo stesso filesystem/disco fisico, quindi il modello dati deve rendere strutturalmente difficile configurare una combinazione media/torrent-target che attraversa dischi diversi.

### Entità

**Disk** (fisico, root a livello di mount point)
- `root_path`: deve essere contenuto in `disk_scan_root` (config.yaml, default `/mnt`) ed esistere come cartella — i mount candidati (bind mount Docker non ancora assegnati a un Disk) si scoprono scansionando `disk_scan_root`, non da un elenco statico
- `torrents_rel_path`: opzionale, relativo a `root_path`. Se assente, l'utente lo crea al volo (vedi File Browser API) come cartella sorella della libreria media su quello stesso disco — mai creata in automatico senza conferma esplicita in UI.
- `st_dev` cachato: valore inode device number rilevato all'ultima verifica, usato per rilevare dischi rimontati/sostituiti in modo silente

**MediaPath** (una cartella di libreria dentro un disco)
- `relative_path`, relativo a `disk.root_path`
- `content_type`: `movie` | `tv` (enum aperta a valori futuri, ma il motore di matching gestisce nativamente solo questi due). Necessario perché la logica di risoluzione cambia: file singolo (movie) vs cartella-stagione con più episodi (tv).
- Un disco può avere zero, una o più `MediaPath` di ciascun tipo (caso "cartella contenitore unica" → una sola `MediaPath` di tipo `tv` che punta a `/disk/media/tv`; caso "strutture sparse" → più `MediaPath`, anche con lo stesso `content_type`, su path diversi dello stesso disco).
- `new_torrent_rel_path`: opzionale, relativo a `disk.root_path` (stessa convenzione di `disk.torrents_rel_path`). Per i client che separano i completed per categoria (es. `.../completed/movies`, `.../completed/tv`): quando impostato, l'esecutore (`app/executor.py::execute_candidate`) crea lì il NUOVO hardlink per questa libreria e comunica quella cartella come `save_path` al client. **Non** restringe il controllo "già in seeding": quello resta sempre sull'intera `disk.torrents_rel_path` (sottocartelle comprese), perché un client può organizzare i completed su più sottocartelle che vanno comunque scansionate tutte per non generare falsi negativi/doppioni — vedi `execute_candidate`, che calcola separatamente uno `scan_root` (sempre `disk.torrents_rel_path`) e un `target_root` (la sottocartella per-libreria se configurata). Deve essere `disk.torrents_rel_path` stesso o una sua sottocartella (validato in `app/api/media_paths.py::set_media_path_new_torrent_path`) — mai un modo per spostare i seed di una libreria fuori dalla cartella torrent del disco. Se assente, si usa `disk.torrents_rel_path` invariato (`MediaPath.effective_new_torrent_rel_path`).

### Path relativi, non assoluti

Tutti i path di configurazione (`torrents_rel_path`, `MediaPath.relative_path`) sono salvati **relativi al `root_path` del disco**, non come path assoluti. Motivazioni:
- se il mount point del disco cambia (es. cambio host, riorganizzazione dei bind mount Docker), si aggiorna un solo campo (`disk.root_path`) invece di ogni `MediaPath`
- rende impossibile per costruzione selezionare, tramite il file browser scoped, un path che risieda fuori dal disco

### Validazione "stesso disco"

Doppio livello:
1. **UX-level (preventivo)**: il file browser di configurazione è scoped al `disk_id` selezionato — l'utente sceglie prima il disco, poi sfoglia solo dentro quella root. Impossibile costruire in UI un `MediaPath`/`torrents_rel_path` che punti altrove.
2. **Runtime (difesa in profondità)**: prima di ogni operazione di hardlink, confronto `st_dev` di sorgente e destinazione. Se un disco è stato rimontato diversamente tra un run e l'altro (o il DB è stato editato a mano), l'operazione viene bloccata con errore esplicito, mai un fallimento silente o un errore cross-device non gestito.

## 4. Configurazione: split YAML / DB

- **`config.yaml`** (statico, richiede restart del container): **solo** `disk_scan_root` (radice sotto cui il container si aspetta i bind mount dei dischi fisici, default `/mnt`) e il path dati dell'app. I singoli dischi NON sono elencati qui.
  ```yaml
  disk_scan_root: /mnt
  data_dir: /app/data
  ```
- **DB** (dinamico, editabile da Web UI senza restart): entità Disk/MediaPath, configurazione tracker (URL, token, adapter type), configurazione client torrent, soglie di confidence, scheduling, tutto il resto. I dischi si aggiungono dalla Web UI scegliendo tra le sottocartelle di `disk_scan_root` trovate come bind mount ma non ancora assegnate a un Disk (`GET /config/disks` calcola questa lista scansionando il filesystem, vedi `app/api/disks.py::list_available_mounts`) — aggiungere un disco fisico richiede solo un nuovo bind mount Docker (Path del template Unraid, o `volumes:` in docker-compose) sotto `disk_scan_root` e un riavvio/ricreazione del container, mai una modifica di `config.yaml`.

Motivazione: i mount Docker sono decisioni a livello di deployment (richiedono comunque un restart per cambiare), tutto il resto deve poter essere modificato senza toccare file o riavviare nulla.

## 5. File Browser API (scoped per disco)

Usato sia per selezionare `MediaPath` che per creare/selezionare `torrents_rel_path`. Un solo meccanismo di scoping condiviso, mai duplicato.

```
GET /api/disks/{disk_id}/browse?path=<relativo, default "">
→ 200 { "disk_id": 3, "current_path": "media/tv", "entries": [{"name": "...", "is_dir": true}, ...] }
→ 400 se il path risolto esce da root_path (traversal o symlink esterno)
→ 404 se disk_id o path non esistono

POST /api/disks/{disk_id}/mkdir   { "path": "torrents" }
→ 201 { "path": "torrents", "created": true }
→ 409 se la cartella esiste già
→ 400 stesso controllo di confinamento del browse

POST /api/disks/{disk_id}/verify
→ 200 { "consistent": true }
→ 200 { "consistent": false, "warning": "..." }   # st_dev cambiato dall'ultima verifica
```

Funzione di scoping condivisa (pseudocodice, va implementata una volta e riusata ovunque):
```python
def resolve_scoped(root_path: str, relative: str) -> str:
    candidate = os.path.realpath(os.path.join(root_path, relative))
    root_real = os.path.realpath(root_path)
    if not (candidate == root_real or candidate.startswith(root_real + os.sep)):
        raise ScopeViolation(candidate)
    return candidate
```

UI: non un dropdown piatto con tutti i path (con dischi grandi sarebbe ingestibile) — un tree browser che apre un livello alla volta, con bottone "Usa questa cartella" / "Crea cartella qui".

## 6. Media Resolver (adapter)

Interfaccia comune:
```python
resolve(file_path: str, content_type: Literal["movie","tv"]) -> MediaItem
  # MediaItem: tmdb_id, season_number?, episode_number?
```

- **Implementazione di default (sempre disponibile)**: parsing del filename (tipo guessit) → titolo/anno/stagione/episodio → lookup TMDB per ID. Nessuna dipendenza esterna oltre l'API TMDB.
- **Implementazione opzionale**: adapter Sonarr/Radarr, che interroga le loro API per un mapping più affidabile (gratis anche size e path esatti). Va offerto come alternativa configurabile, mai assunto presente.

## 7. Tracker Adapter (UNIT3D come prima implementazione)

### API verificate (in fase di ricerca)

- `GET /api/torrents/filter?tmdbId=...&categories[]=...` — ricerca per TMDB ID, filtri opzionali aggiuntivi (risoluzione, categoria, ecc.)
- `GET /api/torrents/:id` — dettaglio singolo torrent
- `GET /api/user` — **solo statistiche aggregate** dell'utente (upload/download totali, ratio, hit&run). **Non** restituisce la lista dei torrent scaricati/in storico.
- Autenticazione: `api_token` come query string, form param, o Bearer token — a scelta. Verificato funzionante con Bearer token contro un'istanza reale (ITT).

**Shape della risposta verificato (ITT, 2026-09-17)**:
- `/api/torrents/filter` → `{"data": [{"type": "torrent", "id": "...", "attributes": {...}}, ...]}`
- `/api/torrents/:id` → `{"type": "torrent", "id": "...", "attributes": {...}}` — **senza** wrapper `data` (diverso dalla lista)
- `attributes` include: `name`, `size` (bytes), `num_file`, `files: [{name, size, ...}]`, `media_info` (output testuale grezzo di mediainfo, non strutturato), `tmdb_id`, `imdb_id`, `tvdb_id`, `category_id`, `type_id`, `resolution_id`, `download_link` (URL autenticato al `.torrent`)
- **`info_hash` non è esposto da nessuno dei due endpoint** — solo `download_link`. `TorrentCandidate.info_hash` resta sempre `None` da questo adapter; il contratto lo prevedeva già come opzionale.

### Limite noto: nessuna API pubblica per lo storico personale

Non esiste un endpoint documentato che restituisca la lista dei torrent scaricati o caricati dall'utente. Quella lista esiste solo come pagina web autenticata (profilo → "history" / "uploads", sezioni separate lato UI). Implicazioni:

- L'accesso allo storico personale (se implementato) deve passare da uno **scraper HTML autenticato**, esplicitamente marcato come fragile/best-effort (si rompe ad ogni cambio di tema/major version del tracker).
- Interfaccia adapter:
  ```python
  search_by_tmdb(tmdb_id: int) -> list[TorrentCandidate]     # sempre richiesto, via API
  get_own_history() -> list[TorrentRecord] | NotSupported     # opzionale, via scraping o API se mai esposta
  ```
- Quando `get_own_history()` non è disponibile o fallisce, il sistema **deve degradare pulito** al motore di matching generale (size + mediainfo), mai bloccarsi o considerarlo un errore fatale.
- Rate limiting e caching locale obbligatori — rispettare i regolamenti del tracker su uso di API/bot.

### Perché lo storico personale è comunque utile (quando disponibile)

Riduce drasticamente l'ambiguità (season pack vs episodio singolo vs complete pack) perché si sa con certezza cosa è stato effettivamente scaricato con quell'account. Va trattato come **fast-path a confidence massima**, non come sostituto del motore generale — il caso "cross-seed di file non scaricati da questo account" resta valido e richiede comunque il motore di ricerca-e-verifica.

## 8. Motore di matching

Pipeline per ogni `media_item`:

1. Se `get_own_history()` disponibile → cerca match diretto per `tmdb_id` (+ season/episode). Se trovato → `source=history`, `confidence` massima.
2. Altrimenti (o in aggiunta, per cross-seed) → `search_by_tmdb(tmdb_id)` sul catalogo, poi per ogni candidato:
   - confronto dimensione file (`size_match`)
   - se le dimensioni combaciano, calcolo/confronto Unique ID mediainfo (`mediainfo_match`) — **attenzione**: l'Unique ID pubblicato dai tracker UNIT3D (estratto testualmente dal blob `media_info`, sezione **General**, cioè a livello di intero container — verificato contro un'istanza reale, non del solo stream video come inizialmente ipotizzato) può comunque non discriminare release con stesso video ma audio diverso (es. doppiaggi diversi) se il muxer lo ricalcola sull'intero file. Trattare quindi il match come **candidato forte**, mai come certezza assoluta. Nota anche che alcune release (es. disco BD completo, cartella `BDMV/...`) non hanno affatto un singolo Unique ID riconducibile: gestire il caso `mediainfo_unique_id is None` senza considerarlo un errore.
3. Calcolo `confidence` esplicito e spiegabile (mai un punteggio ML opaco), es.:
   - `source=history` + tmdb match univoco → confidence 1.0
   - `size_match` + `mediainfo_match` + nome inequivocabile → alta ma non massima
   - `size_match` senza mediainfo conclusivo, o più candidati con stessa size → bassa, richiede revisione
4. Season pack vs episodio: se il torrent candidato è un pack e localmente si ha solo un sottoinsieme degli episodi, non procedere a hardlink automatico — va segnalato come `ambiguity_reason` e richiede decisione esplicita (seed solo se si ha l'intera stagione corrispondente, oppure gestire seeding parziale consapevolmente).

## 9. Confidence e coda di revisione

- Soglia configurabile da UI (default: **0.95**, confermato con l'utente) sopra la quale l'esecuzione (hardlink + seed) è automatica.
- Sotto soglia → `match_review` con stato `pending`, visibile in UI per approvazione/rifiuto manuale.
- Motivazione: un falso positivo che porta a seedare dati non corrispondenti rischia un ban per corrupt/fake data su un tracker privato — molto più grave del semplice mancato hardlink. La certezza euristica non giustifica mai il bypass del controllo umano quando la confidence non è massima.

## 10. Esecuzione: hardlink + seeding

1. Verifica preventiva "già in seeding": controllo `nlink` dell'inode del file locale (non solo verifica del path). Se `nlink > 1`, enumerare via `find <torrents_root> -samefile <file>` per confermare che punti già dentro la cartella di seeding gestita — se sì, skip immediato, nessun lavoro di matching necessario.
2. Verifica `st_dev` sorgente/destinazione (vedi sezione 3).
3. Creazione hardlink con **nome esatto atteso dal tracker** (dal candidate selezionato), nel `torrents_rel_path` del disco.
4. Aggiunta del torrent al client puntando al file appena hardlinkato.
5. **Recheck forzato, mai `skip_checking`.** È un requisito funzionale non negoziabile: protegge da falsi positivi del matching che altrimenti finirebbero silenziosamente a seedare dati sbagliati.
6. Se il recheck fallisce → `seed_job.final_status = failed`, motivo esplicito in `error_message`, mai fallimento silente.

## 11. Modalità di esecuzione

- **Import massivo**: scansione completa una tantum di tutte le `MediaPath` abilitate, per sistemare l'arretrato.
- **Run schedulato**: periodico (cron configurabile da UI), stessa pipeline ma tipicamente su un volume minore di novità.
- Entrambe le modalità condividono lo stesso motore; la differenza è solo nel trigger e nel volume atteso. Ogni run produce una riga in `run_log` con contatori (scansionati, match trovati, auto-seedati, in review, errori).

## 12. Schema DB

Vedi `docs/schema.sql` per lo schema completo (tabelle `disk`, `media_path`, `tracker`, `torrent_client`, `app_settings`, `media_item`, `candidate`, `match_review`, `seed_job`, `run_log`).

## 13. Interfacce adapter (contratti)

Vedi stub in `app/adapters/*/base.py`. Riassunto dei tre contratti:

- **TrackerAdapter**: `search_by_tmdb(tmdb_id)`, `get_own_history()` (può sollevare `NotSupportedError`), gestione rate limit interna.
- **MediaResolverAdapter**: `resolve(file_path, content_type) -> MediaItem`.
- **TorrentClientAdapter**: `add_torrent(torrent_file_or_url, save_path, force_recheck=True)`, `get_torrent_status(info_hash)`.

## 14. Cose esplicitamente aperte (non decise in questa sessione)

- Libreria esatta per il parsing filename
- Se/come implementare concretamente lo scraping storico UNIT3D (rischio di fragilità accettato consapevolmente, non un blocco)
- UI esatta della coda di revisione (mockup non ancora fatto)
