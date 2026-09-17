"""Contratto per gli adapter client torrent.

Vedi docs/SPEC.md sezione 10. Il recheck forzato dopo l'aggiunta del
torrent è un requisito funzionale non negoziabile: protegge da falsi
positivi del motore di matching. Non esporre mai un modo per bypassarlo
implicitamente (es. default a skip_checking=True).
"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

RecheckStatus = Literal["pending", "ok", "failed"]

# Stati nativi qBittorrent che indicano un controllo hash in corso.
_CHECKING_STATES = {"checkingUP", "checkingDL", "checkingResumeData"}
# Stati nativi che indicano un fallimento esplicito (dati mancanti/corrotti).
_ERROR_STATES = {"error", "missingFiles"}


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


class TorrentAddTimeoutError(Exception):
    """Il torrent non è comparso nel client entro il timeout dopo l'aggiunta."""


class QBittorrentAdapter(TorrentClientAdapter):
    """Prima implementazione concreta, via libreria qbittorrent-api.

    NON validata contro un'istanza qBittorrent reale (nessuna disponibile
    in fase di sviluppo) — solo contro un client mockato. Da verificare
    prima di un uso reale, in particolare:
    - la mappatura stato nativo -> RecheckStatus (_CHECKING_STATES/_ERROR_STATES)
    - il timing del polling in _wait_for_new_hash

    torrents_add() non garantisce di ritornare l'info_hash su ogni versione
    dell'API qBittorrent (le versioni più recenti lo fanno, altre no):
    per essere version-agnostic, l'hash viene ricavato confrontando
    l'elenco dei torrent prima/dopo l'aggiunta (diff), mai fidandosi del
    solo valore di ritorno di torrents_add().
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        client=None,
        poll_interval: float = 0.5,
        poll_timeout: float = 15.0,
    ):
        self.base_url = base_url
        self.username = username
        self.password = password
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        if client is not None:
            self._client = client
        else:
            import qbittorrentapi

            self._client = qbittorrentapi.Client(host=base_url, username=username, password=password)

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

        before_hashes = {t.hash for t in self._client.torrents_info()}
        self._client.torrents_add(
            urls=torrent_file_or_url,
            save_path=save_path,
            is_skip_checking=False,
            use_auto_torrent_management=False,
        )
        info_hash = self._wait_for_new_hash(before_hashes)
        self._client.torrents_recheck(torrent_hashes=info_hash)
        return info_hash

    def _wait_for_new_hash(self, before_hashes: set[str]) -> str:
        deadline = time.monotonic() + self.poll_timeout
        while time.monotonic() < deadline:
            current_hashes = {t.hash for t in self._client.torrents_info()}
            new_hashes = current_hashes - before_hashes
            if new_hashes:
                if len(new_hashes) > 1:
                    logger.warning("Più torrent nuovi rilevati dopo add_torrent: %s", new_hashes)
                return next(iter(new_hashes))
            time.sleep(self.poll_interval)
        raise TorrentAddTimeoutError(
            f"Nessun nuovo torrent rilevato in qBittorrent entro {self.poll_timeout}s dall'aggiunta"
        )

    def get_torrent_status(self, info_hash: str) -> TorrentStatus:
        results = self._client.torrents_info(torrent_hashes=info_hash)
        if not results:
            raise ValueError(f"Torrent {info_hash} non trovato nel client")
        torrent = results[0]
        state = torrent.state

        if state in _CHECKING_STATES:
            recheck_status: RecheckStatus = "pending"
        elif state in _ERROR_STATES:
            recheck_status = "failed"
        elif torrent.progress >= 1.0:
            recheck_status = "ok"
        else:
            # Dopo un recheck, dati incompleti significa che il file
            # hardlinkato non corrisponde a quanto atteso dal torrent.
            recheck_status = "failed"

        return TorrentStatus(
            info_hash=torrent.hash, state=state, recheck_status=recheck_status, progress=torrent.progress
        )
