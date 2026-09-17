"""Flusso di scansione: popola media_item per ogni file nelle MediaPath
abilitate, usando il media resolver configurato.

Condiviso da import massivo e run schedulato (docs/SPEC.md sezione 11) —
la differenza tra i due è solo nel trigger/volume, non nel motore.
"""

import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.adapters.media_resolver.base import MediaResolverAdapter
from app.models import MediaItem, MediaPath
from app.seeding_index import build_seeding_index

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m2ts", ".ts", ".wmv", ".mov"}


def iter_video_files(root: str):
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if Path(name).suffix.lower() in VIDEO_EXTENSIONS:
                yield os.path.join(dirpath, name)


def count_enabled_video_files(session: Session) -> int:
    """Precount usato per lo stato live di una run (X/Y scansionati) —
    stesso attraversamento di iter_video_files, senza stat/resolve."""
    media_paths = session.query(MediaPath).filter(MediaPath.enabled.is_(True)).all()
    total = 0
    for media_path in media_paths:
        abs_path = os.path.join(media_path.disk.root_path, media_path.relative_path)
        total += sum(1 for _ in iter_video_files(abs_path))
    return total


def scan_media_path(
    session: Session,
    media_path: MediaPath,
    disk_root_path: str,
    resolver: MediaResolverAdapter,
    on_file_scanned: Callable[[], None] | None = None,
    seeding_index: set[tuple[int, int]] | None = None,
) -> dict[str, int]:
    """Scansiona una singola MediaPath, upsert media_item per ogni file
    trovato. Ritorna i contatori (scanned/resolved/unresolved/already_seeding).
    Committa dopo ogni file (non solo alla fine) cosi' on_file_scanned puo'
    essere usato per aggiornare uno stato live visibile da un'altra sessione.

    seeding_index: (st_dev, inode) dei file già nella cartella torrent del
    disco (vedi app/seeding_index.py) — un file già lì non ha bisogno di
    essere risolto via TMDB: non stiamo cercando di ricollegarlo, è già
    a posto. Il match sul tracker viene poi saltato a sua volta in
    app/matching.py per lo stesso motivo."""
    counts = {"scanned": 0, "resolved": 0, "unresolved": 0, "already_seeding": 0}
    seeding_index = seeding_index or set()
    abs_path = os.path.join(disk_root_path, media_path.relative_path)

    for file_path in iter_video_files(abs_path):
        counts["scanned"] += 1
        stat = os.stat(file_path)

        item = (
            session.query(MediaItem)
            .filter_by(media_path_id=media_path.id, file_path=file_path)
            .one_or_none()
        )
        if item is None:
            item = MediaItem(media_path_id=media_path.id, file_path=file_path, size_bytes=stat.st_size)
            session.add(item)

        item.inode = stat.st_ino
        item.st_dev = stat.st_dev
        item.nlink = stat.st_nlink
        item.size_bytes = stat.st_size
        item.last_scanned_at = datetime.now(timezone.utc)

        already_seeding = stat.st_nlink > 1 and (stat.st_dev, stat.st_ino) in seeding_index
        if already_seeding:
            counts["already_seeding"] += 1
        else:
            try:
                resolved = resolver.resolve(file_path, media_path.content_type)
            except Exception:
                logger.exception("Resolver fallito su %r", file_path)
                resolved = None

            if resolved is not None:
                # Aggiorniamo i campi tmdb solo su successo: un fallimento
                # transitorio del resolver non deve cancellare un match precedente.
                item.tmdb_id = resolved.tmdb_id
                item.season_number = resolved.season_number
                item.episode_number = resolved.episode_number
                item.resolver_source = resolver.SOURCE
                counts["resolved"] += 1
            else:
                counts["unresolved"] += 1

        session.commit()
        if on_file_scanned is not None:
            on_file_scanned()

    return counts


def scan_all_enabled(
    session: Session,
    resolver: MediaResolverAdapter,
    on_file_scanned: Callable[[], None] | None = None,
) -> dict[str, int]:
    """Scansiona tutte le MediaPath abilitate su tutti i dischi configurati."""
    media_paths = session.query(MediaPath).filter(MediaPath.enabled.is_(True)).all()

    totals = {"scanned": 0, "resolved": 0, "unresolved": 0, "already_seeding": 0}
    index_cache: dict[int, set[tuple[int, int]]] = {}
    for media_path in media_paths:
        disk = media_path.disk
        if disk.id not in index_cache:
            index_cache[disk.id] = build_seeding_index(disk.root_path, disk.torrents_rel_path)

        counts = scan_media_path(
            session,
            media_path,
            disk.root_path,
            resolver,
            on_file_scanned=on_file_scanned,
            seeding_index=index_cache[disk.id],
        )
        for key in totals:
            totals[key] += counts[key]
    return totals
