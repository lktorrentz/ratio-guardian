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
    - Rispetta rate_limit_per_min configurato per il tracker; risultati di
      search_by_tmdb cachati in memoria per cache_ttl_seconds.
    """

    _UNIQUE_ID_RE = re.compile(r"Unique ID\s*:\s*(\S+)")

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
        return TorrentCandidate(
            torrent_id_remote=str(item["id"]),
            info_hash=None,
            name=attrs["name"],
            size_bytes=attrs["size"],
            file_list=[f["name"] for f in attrs.get("files") or []],
            mediainfo_unique_id=self._extract_unique_id(attrs.get("media_info")),
        )

    @classmethod
    def _extract_unique_id(cls, media_info: str | None) -> str | None:
        if not media_info:
            return None
        match = cls._UNIQUE_ID_RE.search(media_info)
        return match.group(1) if match else None
