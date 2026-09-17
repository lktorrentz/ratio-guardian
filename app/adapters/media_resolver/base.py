"""Contratto per gli adapter di risoluzione media (file -> TMDB ID).

Vedi docs/SPEC.md sezione 6. L'implementazione filename-based deve sempre
essere disponibile senza dipendenze esterne oltre l'API TMDB; Sonarr/Radarr
sono adapter opzionali, mai assunti presenti.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal


@dataclass
class MediaItem:
    tmdb_id: int
    season_number: int | None  # None per i film
    episode_number: int | None  # None per film o season pack completo


class MediaResolverAdapter(ABC):
    @abstractmethod
    def resolve(
        self, file_path: str, content_type: Literal["movie", "tv"]
    ) -> MediaItem | None:
        """Ritorna None se non è stato possibile identificare il file
        (da loggare, non da far fallire l'intera scansione)."""
        raise NotImplementedError


class FilenameParserResolver(MediaResolverAdapter):
    """Implementazione di default, sempre disponibile. Da valutare in fase
    di sessione: libreria di parsing (guessit, parsett, o altro) — non
    ancora deciso, vedi CLAUDE.md."""

    def __init__(self, tmdb_api_key: str):
        self.tmdb_api_key = tmdb_api_key

    def resolve(
        self, file_path: str, content_type: Literal["movie", "tv"]
    ) -> MediaItem | None:
        raise NotImplementedError(
            "Da implementare: parsing filename -> titolo/anno/stagione/episodio "
            "-> lookup TMDB"
        )


class SonarrRadarrResolver(MediaResolverAdapter):
    """Adapter opzionale. Interroga le API di Sonarr (/api/v3/episodefile)
    o Radarr (/api/v3/moviefile) per un mapping più affidabile, con size e
    path esatti già noti. Mai assunto presente: solo se l'utente configura
    esplicitamente le credenziali."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key

    def resolve(
        self, file_path: str, content_type: Literal["movie", "tv"]
    ) -> MediaItem | None:
        raise NotImplementedError("Da implementare: query Sonarr/Radarr by path")
