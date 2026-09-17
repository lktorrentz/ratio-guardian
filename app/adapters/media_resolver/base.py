"""Contratto per gli adapter di risoluzione media (file -> TMDB ID).

Vedi docs/SPEC.md sezione 6. L'implementazione filename-based deve sempre
essere disponibile senza dipendenze esterne oltre l'API TMDB; Sonarr/Radarr
sono adapter opzionali, mai assunti presenti.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

import httpx
from guessit import guessit

logger = logging.getLogger(__name__)

TMDB_BASE_URL = "https://api.themoviedb.org/3"


@dataclass
class MediaItem:
    tmdb_id: int
    season_number: int | None  # None per i film
    episode_number: int | None  # None per film o season pack completo


class MediaResolverAdapter(ABC):
    #: identificatore salvato in media_item.resolver_source
    SOURCE: str

    @abstractmethod
    def resolve(
        self, file_path: str, content_type: Literal["movie", "tv"]
    ) -> MediaItem | None:
        """Ritorna None se non è stato possibile identificare il file
        (da loggare, non da far fallire l'intera scansione)."""
        raise NotImplementedError


class FilenameParserResolver(MediaResolverAdapter):
    """Implementazione di default, sempre disponibile.

    Parsing del filename via guessit (titolo/anno/stagione/episodio),
    poi lookup TMDB per ottenere il tmdb_id. Nessuna dipendenza esterna
    oltre l'API TMDB.
    """

    SOURCE = "filename_parser"

    def __init__(self, tmdb_api_key: str, http_client: httpx.Client | None = None):
        self.tmdb_api_key = tmdb_api_key
        self._client = http_client or httpx.Client(base_url=TMDB_BASE_URL, timeout=10.0)

    def resolve(
        self, file_path: str, content_type: Literal["movie", "tv"]
    ) -> MediaItem | None:
        guess = guessit(file_path)
        title = self._title_to_str(guess.get("title"))
        if not title:
            logger.warning("guessit non ha trovato un titolo in %r", file_path)
            return None

        tmdb_id = self._search_tmdb(title, self._first(guess.get("year")), content_type)
        if tmdb_id is None:
            logger.warning("Nessun risultato TMDB per %r (%r)", title, file_path)
            return None

        if content_type == "tv":
            season = self._first(guess.get("season"))
            episode = self._first(guess.get("episode"))
        else:
            season = episode = None

        logger.info(
            "Risolto %r -> tmdb_id=%s%s (%r)",
            title,
            tmdb_id,
            f" S{season:02d}E{episode:02d}" if season is not None and episode is not None else "",
            file_path,
        )

        return MediaItem(tmdb_id=tmdb_id, season_number=season, episode_number=episode)

    @staticmethod
    def _first(value):
        """guessit ritorna una lista per i campi multi-valore numerici (es.
        episodi multipli in un unico file): prendiamo il primo, niente di
        più fine."""
        if isinstance(value, list):
            return value[0] if value else None
        return value

    @staticmethod
    def _title_to_str(value):
        """Per il titolo, a differenza di season/episode/year, una lista
        rappresenta frammenti dello stesso titolo (es. ["Daredevil", "Born
        Again"]) da riunire, non alternative da scartare — altrimenti la
        ricerca TMDB perde precisione e rischia falsi positivi."""
        if isinstance(value, list):
            return " ".join(str(v) for v in value)
        return value

    def _search_tmdb(
        self, title: str, year: int | None, content_type: Literal["movie", "tv"]
    ) -> int | None:
        endpoint = "/search/movie" if content_type == "movie" else "/search/tv"
        params: dict[str, str | int] = {"api_key": self.tmdb_api_key, "query": title}
        if year:
            params["year" if content_type == "movie" else "first_air_date_year"] = year

        try:
            response = self._client.get(endpoint, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Ricerca TMDB fallita per %r: %s", title, exc)
            return None

        results = response.json().get("results") or []
        return results[0]["id"] if results else None


class SonarrRadarrResolver(MediaResolverAdapter):
    """Adapter opzionale. Interroga le API di Sonarr (/api/v3/episodefile)
    o Radarr (/api/v3/moviefile) per un mapping più affidabile, con size e
    path esatti già noti. Mai assunto presente: solo se l'utente configura
    esplicitamente le credenziali."""

    SOURCE = "sonarr_radarr"

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key

    def resolve(
        self, file_path: str, content_type: Literal["movie", "tv"]
    ) -> MediaItem | None:
        raise NotImplementedError("Da implementare: query Sonarr/Radarr by path")
