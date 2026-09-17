"""Contratto per gli adapter client torrent.

Vedi docs/SPEC.md sezione 10. Il recheck forzato dopo l'aggiunta del
torrent è un requisito funzionale non negoziabile: protegge da falsi
positivi del motore di matching. Non esporre mai un modo per bypassarlo
implicitamente (es. default a skip_checking=True).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

RecheckStatus = Literal["pending", "ok", "failed"]


@dataclass
class TorrentStatus:
    info_hash: str
    state: str  # stato nativo del client, non normalizzato
    recheck_status: RecheckStatus
    progress: float  # 0.0-1.0


class TorrentClientAdapter(ABC):
    @abstractmethod
    def add_torrent(
        self,
        torrent_file_or_url: str,
        save_path: str,
        force_recheck: bool = True,
    ) -> str:
        """Aggiunge il torrent puntando a save_path (il file già hardlinkato).
        force_recheck deve essere True di default e non deve mai essere
        impostabile a False da nessun chiamante del motore di matching.
        Ritorna l'info_hash del torrent aggiunto."""
        raise NotImplementedError

    @abstractmethod
    def get_torrent_status(self, info_hash: str) -> TorrentStatus:
        raise NotImplementedError


class QBittorrentAdapter(TorrentClientAdapter):
    """Prima implementazione concreta, via libreria qbittorrent-api."""

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url
        self.username = username
        self.password = password
        # TODO: init client qbittorrent-api, login

    def add_torrent(
        self,
        torrent_file_or_url: str,
        save_path: str,
        force_recheck: bool = True,
    ) -> str:
        if not force_recheck:
            raise ValueError(
                "force_recheck=False non è permesso: il recheck reale è "
                "un requisito funzionale, vedi docs/SPEC.md sezione 10."
            )
        raise NotImplementedError("Da implementare: qbittorrent-api add + recheck")

    def get_torrent_status(self, info_hash: str) -> TorrentStatus:
        raise NotImplementedError("Da implementare: qbittorrent-api torrents_info")
