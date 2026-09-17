-- Ratio Guardian — schema DB
-- Vedi docs/SPEC.md per il razionale di ogni tabella/campo.

-- ============ CONFIGURAZIONE ============

CREATE TABLE IF NOT EXISTS disk (
    id                  INTEGER PRIMARY KEY,
    label               TEXT NOT NULL,
    root_path           TEXT NOT NULL UNIQUE,   -- deve combaciare/essere dentro un mount di config.yaml
    st_dev              INTEGER,                -- cachato all'ultima verifica
    torrents_rel_path   TEXT,                   -- relativo a root_path, nullable
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS media_path (
    id              INTEGER PRIMARY KEY,
    disk_id         INTEGER NOT NULL REFERENCES disk(id) ON DELETE CASCADE,
    relative_path   TEXT NOT NULL,          -- relativo a disk.root_path
    content_type    TEXT NOT NULL CHECK (content_type IN ('movie','tv')),
    enabled         BOOLEAN NOT NULL DEFAULT 1,
    UNIQUE(disk_id, relative_path)
);

CREATE TABLE IF NOT EXISTS tracker (
    id                      INTEGER PRIMARY KEY,
    label                   TEXT NOT NULL,
    adapter_type            TEXT NOT NULL,          -- "unit3d", futuri: "gazelle", ecc.
    base_url                TEXT NOT NULL,
    api_token               TEXT NOT NULL,          -- cifrato a riposo
    history_mode            TEXT NOT NULL DEFAULT 'unsupported'
                            CHECK (history_mode IN ('api','scrape','unsupported')),
    history_session_cookie  TEXT,                   -- se history_mode='scrape'
    rate_limit_per_min      INTEGER DEFAULT 30,
    enabled                 BOOLEAN NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS torrent_client (
    id              INTEGER PRIMARY KEY,
    label           TEXT NOT NULL,
    adapter_type    TEXT NOT NULL,          -- "qbittorrent", futuri: "transmission"
    base_url        TEXT NOT NULL,
    username        TEXT,
    password        TEXT,                   -- cifrato a riposo
    enabled         BOOLEAN NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS app_settings (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
    -- es: confidence_threshold_auto=0.95, schedule_cron="0 4 * * *"
);

-- ============ DOMINIO ============

CREATE TABLE IF NOT EXISTS media_item (
    id                      INTEGER PRIMARY KEY,
    media_path_id           INTEGER NOT NULL REFERENCES media_path(id) ON DELETE CASCADE,
    file_path                TEXT NOT NULL,          -- path assoluto risolto al momento della scansione
    inode                    INTEGER,
    st_dev                   INTEGER,
    nlink                    INTEGER,                -- >1 = già hardlinkato altrove, skip veloce
    size_bytes               INTEGER NOT NULL,
    mediainfo_unique_id      TEXT,                   -- calcolato on-demand, cachato
    tmdb_id                  INTEGER,
    season_number            INTEGER,                -- null per movie
    episode_number           INTEGER,                -- null per movie o season pack completo
    resolver_source          TEXT,                   -- "filename_parser" | "sonarr" | "radarr"
    last_scanned_at          TIMESTAMP,
    UNIQUE(media_path_id, file_path)
);

CREATE TABLE IF NOT EXISTS candidate (
    id                  INTEGER PRIMARY KEY,
    media_item_id       INTEGER NOT NULL REFERENCES media_item(id) ON DELETE CASCADE,
    tracker_id          INTEGER NOT NULL REFERENCES tracker(id),
    torrent_id_remote   TEXT NOT NULL,          -- id sul tracker
    info_hash           TEXT,
    name                TEXT NOT NULL,
    size_bytes          INTEGER NOT NULL,
    file_list_json      TEXT,                   -- se disponibile dall'API
    folder              TEXT,                   -- sottocartella del pack (UNIT3D "folder"), null per file singolo
    download_link       TEXT,                   -- URL autenticato al .torrent (necessario per add_torrent)
    source              TEXT NOT NULL CHECK (source IN ('history','catalog_search')),
    size_match          BOOLEAN,
    mediainfo_match     BOOLEAN,
    confidence          REAL NOT NULL,          -- 0.0-1.0, calcolata da regole esplicite
    ambiguity_reason    TEXT,                   -- es. "season_pack_partial", "multiple_size_matches"
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS match_review (
    id              INTEGER PRIMARY KEY,
    candidate_id    INTEGER NOT NULL REFERENCES candidate(id),
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','approved','rejected','auto_approved')),
    decided_by      TEXT,                   -- "system" | username
    decided_at      TIMESTAMP
);

CREATE TABLE IF NOT EXISTS seed_job (
    id                      INTEGER PRIMARY KEY,
    candidate_id            INTEGER NOT NULL REFERENCES candidate(id),
    hardlink_path           TEXT,                   -- file singolo: path del file; pack: cartella contenitore
    hardlink_created_at     TIMESTAMP,
    torrent_added_at        TIMESTAMP,
    info_hash               TEXT,                   -- noto solo dopo l'aggiunta al client (mai dal tracker)
    recheck_status          TEXT CHECK (recheck_status IN ('pending','ok','failed')),
    final_status            TEXT NOT NULL DEFAULT 'in_progress'
                            CHECK (final_status IN ('in_progress','seeding','failed','rolled_back')),
    error_message           TEXT
);

CREATE TABLE IF NOT EXISTS run_log (
    id              INTEGER PRIMARY KEY,
    run_type        TEXT NOT NULL CHECK (run_type IN ('scheduled','manual','bulk_import')),
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    items_total     INTEGER,          -- precontato all'avvio del run, per lo stato live (X/Y)
    items_scanned   INTEGER DEFAULT 0,
    matches_found   INTEGER DEFAULT 0,
    auto_seeded     INTEGER DEFAULT 0,
    pending_review  INTEGER DEFAULT 0,
    errors          INTEGER DEFAULT 0
);
