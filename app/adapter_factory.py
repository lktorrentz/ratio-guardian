"""Costruisce le istanze concrete degli adapter a partire dalla
configurazione nel DB.

Vedi CLAUDE.md: solo i mount point vivono in config.yaml, tutto il resto
(tracker, client torrent, credenziali, tmdb key, soglie) vive nel DB ed è
editabile da UI senza restart.
"""

from sqlalchemy.orm import Session

from app.adapters.media_resolver.base import FilenameParserResolver, MediaResolverAdapter
from app.adapters.torrent_client.base import QBittorrentAdapter, TorrentClientAdapter
from app.adapters.tracker.base import TrackerAdapter, Unit3dTrackerAdapter
from app.models import Tracker, TorrentClient
from app.settings_repo import get_setting


def build_tracker_adapter(tracker: Tracker) -> TrackerAdapter:
    if tracker.adapter_type == "unit3d":
        return Unit3dTrackerAdapter(
            base_url=tracker.base_url,
            api_token=tracker.api_token,
            rate_limit_per_min=tracker.rate_limit_per_min or 30,
        )
    raise ValueError(f"adapter_type tracker non supportato: {tracker.adapter_type!r}")


def build_torrent_client_adapter(torrent_client: TorrentClient) -> TorrentClientAdapter:
    if torrent_client.adapter_type == "qbittorrent":
        return QBittorrentAdapter(
            base_url=torrent_client.base_url,
            username=torrent_client.username,
            password=torrent_client.password,
        )
    raise ValueError(f"adapter_type torrent_client non supportato: {torrent_client.adapter_type!r}")


def build_media_resolver(session: Session) -> MediaResolverAdapter:
    tmdb_api_key = get_setting(session, "tmdb_api_key")
    if not tmdb_api_key:
        raise ValueError("tmdb_api_key non configurata in app_settings")
    return FilenameParserResolver(tmdb_api_key=tmdb_api_key)
