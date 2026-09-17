"""Motore di matching: per ogni media_item risolto (tmdb_id noto), cerca
candidati sul tracker e scrive righe in `candidate` con una confidence
esplicita e spiegabile (mai un punteggio ML opaco).

Vedi docs/SPEC.md sezioni 8-9.
"""

import json
import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.adapters.tracker.base import (
    NotSupportedError,
    TorrentCandidate,
    TorrentRecord,
    TrackerAdapter,
)
from app.mediainfo_util import compute_unique_id
from app.review import create_review_for_candidates
from app.scanner import VIDEO_EXTENSIONS
from app.seeding_index import build_seeding_index
from app.models import Candidate, MediaItem, Tracker

logger = logging.getLogger(__name__)

# Regole esplicite (docs/SPEC.md sezione 9), non un punteggio ML.
CONFIDENCE_HISTORY = 1.0
CONFIDENCE_SIZE_AND_MEDIAINFO_MATCH = 0.9
CONFIDENCE_SIZE_ONLY = 0.5
CONFIDENCE_NO_MATCH = 0.0


def run_matching(
    session: Session,
    tracker_row: Tracker,
    tracker_adapter: TrackerAdapter,
    media_items: list[MediaItem] | None = None,
    on_item_matched: Callable[[], None] | None = None,
) -> dict[str, int]:
    """Esegue il matching per una lista di media_item (default: tutti quelli
    con tmdb_id già risolto). Ritorna contatori aggregati.

    Salta la ricerca sul tracker per i media_item già rilevati "in seeding"
    (stesso indice (st_dev, inode) usato in app/scanner.py): niente da
    ricollegare, niente da cercare — vedi docs/SPEC.md sezione 10 punto 1."""
    if media_items is None:
        media_items = session.query(MediaItem).filter(MediaItem.tmdb_id.isnot(None)).all()

    history = _get_history(tracker_adapter)
    index_cache: dict[int, set[tuple[int, int]]] = {}

    totals = {
        "media_items": 0,
        "candidates": 0,
        "auto_approved": 0,
        "pending_review": 0,
        "already_seeding": 0,
    }
    for media_item in media_items:
        if _is_already_seeding(media_item, index_cache):
            totals["already_seeding"] += 1
            if on_item_matched is not None:
                on_item_matched()
            continue

        candidates = match_media_item(session, media_item, tracker_row, tracker_adapter, history=history)
        totals["media_items"] += 1
        totals["candidates"] += len(candidates)

        review = create_review_for_candidates(session, candidates)
        if review is not None:
            if review.status == "auto_approved":
                totals["auto_approved"] += 1
            elif review.status == "pending":
                totals["pending_review"] += 1

        if on_item_matched is not None:
            on_item_matched()
    return totals


def _is_already_seeding(media_item: MediaItem, index_cache: dict[int, set[tuple[int, int]]]) -> bool:
    if not media_item.nlink or media_item.nlink <= 1 or media_item.st_dev is None or media_item.inode is None:
        return False
    disk = media_item.media_path.disk
    if disk.id not in index_cache:
        index_cache[disk.id] = build_seeding_index(disk.root_path, disk.torrents_rel_path)
    return (media_item.st_dev, media_item.inode) in index_cache[disk.id]


def match_media_item(
    session: Session,
    media_item: MediaItem,
    tracker_row: Tracker,
    tracker_adapter: TrackerAdapter,
    history: list[TorrentRecord] | None = None,
) -> list[Candidate]:
    if media_item.tmdb_id is None:
        return []

    history_record = _find_in_history(history, media_item) if history else None
    if history_record is not None:
        candidate = _persist_candidate(
            session,
            media_item,
            tracker_row,
            TorrentCandidate(
                torrent_id_remote=history_record.torrent_id_remote,
                info_hash=history_record.info_hash,
                name=history_record.name,
                size_bytes=history_record.size_bytes,
                file_list=history_record.file_list,
                mediainfo_unique_id=None,
            ),
            source="history",
            confidence=CONFIDENCE_HISTORY,
        )
        session.commit()
        return [candidate]

    torrent_candidates = tracker_adapter.search_by_tmdb(media_item.tmdb_id)

    persisted = []
    for tc in torrent_candidates:
        size_match = tc.size_bytes == media_item.size_bytes
        mediainfo_match = None

        if size_match:
            local_unique_id = _get_local_unique_id(session, media_item)
            if local_unique_id is not None and tc.mediainfo_unique_id is not None:
                mediainfo_match = local_unique_id == tc.mediainfo_unique_id

        confidence, ambiguity_reason = _score(media_item, tc, size_match, mediainfo_match, torrent_candidates)

        persisted.append(
            _persist_candidate(
                session,
                media_item,
                tracker_row,
                tc,
                source="catalog_search",
                confidence=confidence,
                size_match=size_match,
                mediainfo_match=mediainfo_match,
                ambiguity_reason=ambiguity_reason,
            )
        )
    session.commit()  # un solo commit per media_item, non uno per candidate
    return persisted


def _get_history(tracker_adapter: TrackerAdapter) -> list[TorrentRecord] | None:
    """Degrada pulito (None) se lo storico non è supportato — mai un
    errore fatale, vedi docs/SPEC.md sezione 7."""
    try:
        return tracker_adapter.get_own_history()
    except NotSupportedError:
        return None


def _find_in_history(history: list[TorrentRecord], media_item: MediaItem) -> TorrentRecord | None:
    # NOTA: TorrentRecord non porta season/episode (gap del contratto in
    # app/adapters/tracker/base.py rispetto a quanto descritto in
    # docs/SPEC.md sezione 8 punto 1) — oggi innocuo perché get_own_history()
    # non è mai implementato (sempre NotSupportedError), ma da rivedere se
    # e quando lo storico verrà davvero implementato.
    matches = [r for r in history if r.tmdb_id == media_item.tmdb_id]
    return matches[0] if len(matches) == 1 else None


def _get_local_unique_id(session: Session, media_item: MediaItem) -> str | None:
    if media_item.mediainfo_unique_id is not None:
        return media_item.mediainfo_unique_id
    unique_id = compute_unique_id(media_item.file_path)
    if unique_id is not None:
        media_item.mediainfo_unique_id = unique_id
        session.commit()
    return unique_id


def _score(
    media_item: MediaItem,
    tc: TorrentCandidate,
    size_match: bool,
    mediainfo_match: bool | None,
    all_candidates: list[TorrentCandidate],
) -> tuple[float, str | None]:
    if not size_match:
        return CONFIDENCE_NO_MATCH, None

    if mediainfo_match is False:
        # dimensione combacia ma il contenuto reale no: falso positivo,
        # non un candidato debole da mandare comunque in review.
        return CONFIDENCE_NO_MATCH, "mediainfo_mismatch"

    same_size_count = sum(1 for c in all_candidates if c.size_bytes == media_item.size_bytes)
    unambiguous_size = same_size_count == 1

    ambiguity_reason = None
    if media_item.episode_number is not None:
        video_file_count = sum(1 for name in (tc.file_list or []) if _is_video(name))
        if video_file_count > 1:
            ambiguity_reason = "season_pack_partial"

    if mediainfo_match is True and unambiguous_size and ambiguity_reason is None:
        return CONFIDENCE_SIZE_AND_MEDIAINFO_MATCH, None

    if ambiguity_reason is None and not unambiguous_size:
        ambiguity_reason = "multiple_size_matches"

    return CONFIDENCE_SIZE_ONLY, ambiguity_reason


def _is_video(filename: str) -> bool:
    return any(filename.lower().endswith(ext) for ext in VIDEO_EXTENSIONS)


def _persist_candidate(
    session: Session,
    media_item: MediaItem,
    tracker_row: Tracker,
    tc: TorrentCandidate,
    *,
    source: str,
    confidence: float,
    size_match: bool | None = None,
    mediainfo_match: bool | None = None,
    ambiguity_reason: str | None = None,
) -> Candidate:
    candidate = Candidate(
        media_item_id=media_item.id,
        tracker_id=tracker_row.id,
        torrent_id_remote=tc.torrent_id_remote,
        info_hash=tc.info_hash,
        name=tc.name,
        size_bytes=tc.size_bytes,
        file_list_json=json.dumps(tc.file_list) if tc.file_list is not None else None,
        folder=tc.folder,
        download_link=tc.download_link,
        source=source,
        size_match=size_match,
        mediainfo_match=mediainfo_match,
        confidence=confidence,
        ambiguity_reason=ambiguity_reason,
    )
    session.add(candidate)
    # Niente commit qui: chiamato una volta per candidato, committare a
    # ogni chiamata moltiplicherebbe inutilmente le transazioni di
    # scrittura. Il chiamante (match_media_item) committa una volta sola
    # dopo aver aggiunto tutti i candidati di un media_item.
    return candidate
