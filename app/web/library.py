"""Pagina web 'Libreria': vista completa di tutti i file scansionati, non
solo quelli passati per una decisione — chi è già collegato/seeding
correttamente, e chi è orfano con lo stato del tentativo più recente
(in attesa di revisione, in corso, fallito, rifiutato, o senza alcun
candidato trovato). Vedi app/library.py per la logica di stato/gruppo."""

import json
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import review as review_service
from app.deps import get_session
from app.executor import ExecutionError
from app.library import Entry, list_library_groups
from app.models import SeedJob, TorrentClient
from app.scanner import VIDEO_EXTENSIONS
from app.web.templates import templates

router = APIRouter()

STATUS_LABELS = {
    "seeding": "Seeding",
    "pending": "In attesa di revisione",
    "in_progress": "In verifica",
    "failed": "Fallito",
    "rejected": "Rifiutato",
    "unmatched": "Nessun match trovato",
    "unknown": "Approvato (non eseguito)",
}

STATUS_FILTERS = ("all", "orphan", "seeding", "pending", "in_progress", "failed", "rejected", "unmatched", "unknown")
FILTER_LABELS = {"all": "Tutti", "orphan": "Orfani"}


def _is_video(filename: str) -> bool:
    return any(filename.lower().endswith(ext) for ext in VIDEO_EXTENSIONS)


def _tracker_url(candidate) -> str | None:
    """Pattern standard delle istanze UNIT3D — verificato in docs/SPEC.md
    sezione 7 per i dettagli API, non per questo specifico URL di
    visualizzazione (a differenza degli endpoint API, non testato contro
    un'istanza reale: se il tuo tracker usa un path diverso, va adattato)."""
    if candidate is None:
        return None
    return f"{candidate.tracker.base_url.rstrip('/')}/torrents/{candidate.torrent_id_remote}"


def _client_url(torrent_client: TorrentClient | None, info_hash: str | None) -> str | None:
    """Deep-link al torrent specifico nella WebUI — verificato contro il
    routing di VueTorrent (#/torrent/:hash), la WebUI moderna di default
    da qBittorrent 5.0. Su una WebUI diversa (skin legacy, altra WebUI
    alternativa) il link porta comunque alla home della WebUI, mai a un
    errore: nessun modo di sapere quale skin è in uso da qui."""
    if torrent_client is None or not info_hash:
        return None
    return f"{torrent_client.base_url.rstrip('/')}/#/torrent/{info_hash}"


def _tmdb_url(media_item) -> str | None:
    if media_item.tmdb_id is None:
        return None
    kind = "tv" if media_item.media_path.content_type == "tv" else "movie"
    return f"https://www.themoviedb.org/{kind}/{media_item.tmdb_id}"


def _naive(dt: datetime | None) -> datetime | None:
    """created_at (SQLite CURRENT_TIMESTAMP) è naive, decided_at/
    last_scanned_at sono aware (datetime.now(timezone.utc)) — normalizzati
    per poterli confrontare tra loro nell'ordinamento senza un
    TypeError."""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _group_seed_job(group: list[Entry]):
    for _, _, review in group:
        if review is not None and review.candidate.seed_jobs:
            return review.candidate.seed_jobs[0]
    return None


def _group_view(group: list[Entry], torrent_client: TorrentClient | None) -> dict:
    primary_item, status, primary_review = group[0]
    candidate = primary_review.candidate if primary_review is not None else None
    disk = primary_item.media_path.disk

    is_pack = False
    if candidate is not None:
        file_list = json.loads(candidate.file_list_json) if candidate.file_list_json else []
        video_files = [f for f in file_list if _is_video(f)]
        is_pack = len(video_files) > 1

    if is_pack:
        media_path_display = f"{primary_item.media_path.relative_path} · {len(group)} episodi"
    else:
        media_path_display = primary_item.file_path

    seed_job = _group_seed_job(group)
    decided_at = max(
        (r.decided_at for _, _, r in group if r is not None and r.decided_at is not None), default=None
    )
    sort_key = (
        _naive(decided_at)
        or _naive(candidate.created_at if candidate is not None else None)
        or _naive(primary_item.last_scanned_at)
        or datetime.min
    )

    return {
        "disk": disk,
        "is_pack": is_pack,
        "episode_count": len(group),
        "media_path_display": media_path_display,
        "torrent_path_display": seed_job.hardlink_path if seed_job else None,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "seed_job": seed_job,
        "sort_key": sort_key,
        "tracker_label": candidate.tracker.label if candidate is not None else None,
        "tracker_url": _tracker_url(candidate),
        "client_url": _client_url(torrent_client, seed_job.info_hash if seed_job else None),
        "tmdb_url": _tmdb_url(primary_item),
    }


@router.get("/library")
def library_page(request: Request, status: str = "all", session: Session = Depends(get_session)):
    if status not in STATUS_FILTERS:
        status = "all"
    groups = list_library_groups(session, status=status)
    torrent_client = session.query(TorrentClient).filter_by(enabled=True).first()
    views = [_group_view(g, torrent_client) for g in groups]
    views.sort(key=lambda v: v["sort_key"], reverse=True)
    return templates.TemplateResponse(
        request,
        "library.html",
        {
            "views": views,
            "status": status,
            "status_labels": STATUS_LABELS,
            "filter_labels": FILTER_LABELS,
            "status_filters": STATUS_FILTERS,
        },
    )


@router.post("/library/failed/{seed_job_id}/retry")
def retry_failed_seed_job_from_library(seed_job_id: int, session: Session = Depends(get_session)):
    seed_job = session.get(SeedJob, seed_job_id)
    if seed_job is not None and seed_job.final_status == "failed":
        try:
            review_service.retry_failed(session, seed_job)
        except ExecutionError:
            pass  # resta failed con il nuovo error_message, visibile nella pagina
    return RedirectResponse(url=f"/library?status={quote('failed')}", status_code=303)
