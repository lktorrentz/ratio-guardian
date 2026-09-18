"""Contratto per gli adapter tracker.

Vedi docs/SPEC.md sezione 7 per il razionale, in particolare il limite noto:
non esiste API pubblica documentata per lo storico personale su UNIT3D —
get_own_history() è opzionale e può non essere supportato.
"""

import logging
import re
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from typing import Literal

import httpx

logger = logging.getLogger(__name__)


class NotSupportedError(Exception):
    """Sollevata quando l'adapter non supporta una funzionalità (es. storico
    personale non disponibile via API per questo tracker/istanza)."""


@dataclass
class TorrentCandidate:
    torrent_id_remote: str
    info_hash: str | None
    name: str
    size_bytes: int
    file_list: list[str] | None  # None se il tracker non espone la struttura file
    mediainfo_unique_id: str | None
    folder: str | None = None  # sottocartella del pack (UNIT3D "folder"), None per file singolo
    download_link: str | None = None  # URL autenticato al .torrent, necessario per aggiungerlo al client
    file_sizes: dict[str, int] | None = None  # nome file -> dimensione in byte, se il tracker lo espone
    # (es. UNIT3D files[].size) — indispensabile per valutare un season pack:
    # size_bytes è la dimensione dell'INTERO torrent, mai confrontabile con
    # un singolo episodio locale (vedi app/matching.py::_effective_candidate_size).
    mediainfo_unique_ids_by_filename: dict[str, str] | None = None  # nome file (basename) -> Unique ID,
    # per i season pack il blob media_info è la concatenazione dei report
    # per-file: mediainfo_unique_id da solo cattura solo il primo (vedi
    # Unit3dTrackerAdapter._extract_unique_ids_by_filename e
    # app/matching.py::_pack_mediainfo_match).


@dataclass
class TorrentRecord:
    """Voce dello storico personale (download o upload)."""
    torrent_id_remote: str
    info_hash: str | None
    name: str
    size_bytes: int
    tmdb_id: int | None
    source: Literal["uploaded", "downloaded"]
    file_list: list[str] | None


class TrackerAdapter(ABC):
    """Un'istanza per ogni tracker configurato (vedi tabella `tracker`)."""

    @abstractmethod
    def search_by_tmdb(self, tmdb_id: int) -> list[TorrentCandidate]:
        """Ricerca sul catalogo pubblico del tracker. Sempre richiesto:
        è il fallback generale usato anche per il cross-seed di file mai
        scaricati con questo account."""
        raise NotImplementedError

    def get_own_history(self) -> list[TorrentRecord]:
        """Storico personale (upload + download), se il tracker/adapter lo
        supporta. Solleva NotSupportedError se non disponibile: il chiamante
        deve gestire questo caso degradando al motore di matching generale,
        mai trattarlo come errore fatale."""
        raise NotSupportedError(f"{self.__class__.__name__} non supporta get_own_history()")


class _RateLimiter:
    """Sliding window semplice: al massimo `max_per_min` richieste in ogni
    finestra di 60s, bloccante (time.sleep) oltre soglia."""

    def __init__(self, max_per_min: int):
        self.max_per_min = max_per_min
        self._timestamps: deque[float] = deque()

    def wait(self) -> None:
        now = time.monotonic()
        window_start = now - 60
        while self._timestamps and self._timestamps[0] < window_start:
            self._timestamps.popleft()
        if len(self._timestamps) >= self.max_per_min:
            sleep_for = 60 - (now - self._timestamps[0])
            if sleep_for > 0:
                time.sleep(sleep_for)
        self._timestamps.append(time.monotonic())


class Unit3dTrackerAdapter(TrackerAdapter):
    """Prima implementazione concreta. Shape della risposta verificata contro
    un'istanza reale (ITT):

    - GET /api/torrents/filter?tmdbId=...
      -> {"data": [{"type": "torrent", "id": "...", "attributes": {...}}, ...]}
    - GET /api/torrents/:id
      -> {"type": "torrent", "id": "...", "attributes": {...}}   (NIENTE wrapper "data")
    - GET /api/user -> SOLO statistiche aggregate (upload/download totali,
      ratio, hit&run). NON restituisce la lista dei torrent.
    - Nessun endpoint pubblico documentato per la lista storico personale:
      quella pagina esiste solo come HTML autenticato (profilo -> history/uploads).
      get_own_history(), se implementato, deve passare da uno scraper HTML
      esplicitamente marcato come fragile (si rompe a ogni cambio tema/versione)
      e con caching/sync incrementale locale.
    - Autenticazione: Bearer token (verificato funzionante). L'API supporta anche
      api_token come query string o form param, ma l'header evita che il token
      finisca in URL/log.
    - `info_hash` NON è esposto da questi endpoint (solo `download_link`,
      un URL autenticato al file .torrent) — TorrentCandidate.info_hash resta
      sempre None da questo adapter, il contratto lo prevede già come opzionale.
    - `attributes.media_info` è l'output testuale grezzo di mediainfo: lo
      Unique ID va estratto con una regex, non è un campo strutturato.
      Nell'esempio verificato compare nella sezione General (a livello di
      intero container), non solo nello stream video come inizialmente
      ipotizzato in docs/SPEC.md sezione 8 — trattarlo comunque come
      candidato forte, mai come certezza assoluta (vedi motore di matching).
      Per un season pack, questo blob è la CONCATENAZIONE dei report
      mediainfo di ciascun file del pack (uno per episodio, ognuno con la
      propria sezione General che include "Complete name": il nome del
      file a cui quella sezione si riferisce) — mediainfo_unique_id da
      solo cattura solo il primo Unique ID dell'intero blob (il primo
      file), _extract_unique_ids_by_filename() li estrae tutti indicizzati
      per nome file, cosicché il motore di matching possa isolare quello
      del file dell'episodio cercato invece di quello (sbagliato) del
      primo file del pack.
    - Rispetta rate_limit_per_min configurato per il tracker; risultati di
      search_by_tmdb cachati in memoria per cache_ttl_seconds.
    """

    _UNIQUE_ID_RE = re.compile(r"Unique ID\s*:\s*(\S+)")
    _GENERAL_SECTION_RE = re.compile(r"(?m)^\s*General\s*$")
    _COMPLETE_NAME_RE = re.compile(r"Complete name\s*:\s*(.+)")

    def __init__(
        self,
        base_url: str,
        api_token: str,
        rate_limit_per_min: int = 30,
        http_client: httpx.Client | None = None,
        cache_ttl_seconds: int = 600,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self._client = http_client or httpx.Client(base_url=self.base_url, timeout=15.0)
        self._rate_limiter = _RateLimiter(rate_limit_per_min)
        self._cache_ttl_seconds = cache_ttl_seconds
        self._search_cache: dict[int, tuple[float, list[TorrentCandidate]]] = {}

    def search_by_tmdb(self, tmdb_id: int) -> list[TorrentCandidate]:
        cached = self._search_cache.get(tmdb_id)
        if cached is not None and (time.monotonic() - cached[0]) < self._cache_ttl_seconds:
            return cached[1]

        response = self._get("/api/torrents/filter", params={"tmdbId": tmdb_id})
        candidates = [self._to_candidate(item) for item in response.json().get("data", [])]
        self._search_cache[tmdb_id] = (time.monotonic(), candidates)
        return candidates

    def get_torrent_detail(self, torrent_id_remote: str) -> TorrentCandidate:
        """Dettaglio singolo torrent. In pratica /filter già ritorna lo
        stesso shape di attributi, ma questo endpoint è utile per rileggere
        lo stato aggiornato di un candidate specifico senza rifare una
        ricerca completa."""
        response = self._get(f"/api/torrents/{torrent_id_remote}")
        return self._to_candidate(response.json())

    def get_own_history(self) -> list[TorrentRecord]:
        # TODO: valutare se implementare lo scraping (history_mode='scrape' in DB)
        # oppure lasciare esplicitamente NotSupportedError finché non richiesto.
        raise NotSupportedError(
            "Storico personale non esposto via API pubblica su UNIT3D; "
            "richiede scraper HTML dedicato, non ancora implementato."
        )

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        self._rate_limiter.wait()
        response = self._client.get(
            path, params=params, headers={"Authorization": f"Bearer {self.api_token}"}
        )
        response.raise_for_status()
        return response

    def _to_candidate(self, item: dict) -> TorrentCandidate:
        attrs = item["attributes"]
        files = attrs.get("files") or []
        folder, file_list, file_sizes = self._normalize_pack_structure(attrs.get("folder"), files)
        return TorrentCandidate(
            torrent_id_remote=str(item["id"]),
            info_hash=None,
            name=attrs["name"],
            size_bytes=attrs["size"],
            file_list=file_list,
            mediainfo_unique_id=self._extract_unique_id(attrs.get("media_info")),
            folder=folder,
            download_link=attrs.get("download_link"),
            file_sizes=file_sizes,
            mediainfo_unique_ids_by_filename=self._extract_unique_ids_by_filename(attrs.get("media_info")),
        )

    @staticmethod
    def _normalize_pack_structure(
        raw_folder: str | None, files: list[dict]
    ) -> tuple[str | None, list[str], dict[str, int]]:
        """Determina la cartella del pack (se ricavabile qui) e normalizza
        file_list/file_sizes a nomi nudi — il resto del motore (matching,
        esecutore) assume SEMPRE folder e nome file separati, mai un path
        già annidato dentro file_list.

        Alcune istanze UNIT3D riportano il path relativo già dentro
        files[].name (es. "Release.Name/Show.S01E02.mkv") invece che nella
        sola attributes.folder: se tutti i file condividono lo stesso
        primo componente di path, quella è la cartella vera — va tolta dal
        nome e MAI anche aggiunta separatamente (altrimenti si otterrebbe
        un path raddoppiato all'esecuzione).

        Se invece manca sia attributes.folder sia un path annidato,
        NON si indovina più una cartella da attributes.name: è un titolo
        "leggibile" per la UI (spazi), non necessariamente il vero nome di
        release scritto nel .torrent (punti) — usarlo produceva un path
        che il client non riconosceva, causando un mismatch e quindi un
        recheck fallito (osservato in produzione: risalita al 99% e poi
        fallimento). In questo caso folder resta None qui; l'unica fonte
        davvero affidabile è il .torrent stesso, letto da
        app/executor.py al momento dell'esecuzione (vedi
        app/torrent_file.py) — mai un fallimento silente."""
        names = [f["name"] for f in files]

        embedded_folder = None
        if names and all("/" in name for name in names):
            first_components = {name.split("/", 1)[0] for name in names}
            if len(first_components) == 1:
                embedded_folder = next(iter(first_components))

        if embedded_folder:
            prefix = embedded_folder + "/"
            stripped_names = [name[len(prefix):] if name.startswith(prefix) else name for name in names]
            file_sizes = {
                (f["name"][len(prefix):] if f["name"].startswith(prefix) else f["name"]): f["size"]
                for f in files
                if "size" in f
            }
            return embedded_folder, stripped_names, file_sizes

        file_sizes = {f["name"]: f["size"] for f in files if "size" in f}
        return raw_folder, names, file_sizes

    @classmethod
    def _extract_unique_id(cls, media_info: str | None) -> str | None:
        if not media_info:
            return None
        match = cls._UNIQUE_ID_RE.search(media_info)
        return match.group(1) if match else None

    @classmethod
    def _extract_unique_ids_by_filename(cls, media_info: str | None) -> dict[str, str]:
        """Un blob media_info di un season pack è la concatenazione dei
        report mediainfo di ciascun file, ciascuno con la propria sezione
        General (Complete name + Unique ID). Ritorna {basename: unique_id}
        per ogni sezione dove entrambi i campi sono presenti — una sezione
        priva di "Complete name" o "Unique ID" (es. container che non lo
        espone) è semplicemente esclusa, mai un errore."""
        if not media_info:
            return {}
        result: dict[str, str] = {}
        # Il primo elemento dello split è il preambolo prima del primo
        # "General" (vuoto nel caso comune), va scartato.
        for section in cls._GENERAL_SECTION_RE.split(media_info)[1:]:
            name_match = cls._COMPLETE_NAME_RE.search(section)
            id_match = cls._UNIQUE_ID_RE.search(section)
            if name_match and id_match:
                filename = re.split(r"[\\/]", name_match.group(1).strip())[-1]
                result[filename] = id_match.group(1)
        return result
