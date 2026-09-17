"""Contratto per gli adapter tracker.

Vedi docs/SPEC.md sezione 7 per il razionale, in particolare il limite noto:
non esiste API pubblica documentata per lo storico personale su UNIT3D —
get_own_history() è opzionale e può non essere supportato.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal


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


class Unit3dTrackerAdapter(TrackerAdapter):
    """Prima implementazione concreta. Note dalla ricerca in fase di design:

    - GET /api/torrents/filter?tmdbId=...  -> ricerca per TMDB ID (endpoint verificato)
    - GET /api/torrents/:id                -> dettaglio torrent
    - GET /api/user                        -> SOLO statistiche aggregate (upload/download
      totali, ratio, hit&run). NON restituisce la lista dei torrent.
    - Nessun endpoint pubblico documentato per la lista storico personale:
      quella pagina esiste solo come HTML autenticato (profilo -> history/uploads).
      get_own_history(), se implementato, deve passare da uno scraper HTML
      esplicitamente marcato come fragile (si rompe a ogni cambio tema/versione)
      e con caching/sync incrementale locale.
    - Autenticazione: api_token come query string, form param, o Bearer token.
    - Rispettare rate_limit_per_min configurato per il tracker.
    """

    def __init__(self, base_url: str, api_token: str, rate_limit_per_min: int = 30):
        self.base_url = base_url
        self.api_token = api_token
        self.rate_limit_per_min = rate_limit_per_min
        # TODO: client HTTP (httpx) con rate limiting e caching locale

    def search_by_tmdb(self, tmdb_id: int) -> list[TorrentCandidate]:
        raise NotImplementedError("Da implementare: GET /api/torrents/filter?tmdbId=...")

    def get_own_history(self) -> list[TorrentRecord]:
        # TODO: valutare se implementare lo scraping (history_mode='scrape' in DB)
        # oppure lasciare esplicitamente NotSupportedError finché non richiesto.
        raise NotSupportedError(
            "Storico personale non esposto via API pubblica su UNIT3D; "
            "richiede scraper HTML dedicato, non ancora implementato."
        )
