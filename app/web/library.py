"""Pagina web 'Libreria': storico di tutto ciò che è già stato deciso
(seeding, fallito, o rifiutato) — mai i pending, quelli restano solo in
/reviews (vedi app/library.py e CLAUDE.md: niente duplicazione tra le
due pagine)."""

import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import review as review_service
from app.deps import get_session
from app.executor import ExecutionError
from app.library import group_seed_job, group_status, list_library_groups
from app.models import MatchReview, SeedJob, TorrentClient
from app.scanner import VIDEO_EXTENSIONS
from app.web.templates import templates

router = APIRouter()

STATUS_LABELS = {
    "seeding": "Seeding",
    "failed": "Fallito",
    "in_progress": "In verifica",
    "rejected": "Rifiutato",
    "unknown": "Approvato (non eseguito)",
}

STATUS_FILTERS = ("all", "seeding", "failed", "in_progress", "rejected", "unknown")


def _is_video(filename: str) -> bool:
    return any(filename.lower().endswith(ext) for ext in VIDEO_EXTENSIONS)


def _tracker_url(candidate) -> str:
    """Pattern standard delle istanze UNIT3D — verificato in docs/SPEC.md
    sezione 7 per i dettagli API, non per questo specifico URL di
    visualizzazione (a differenza degli endpoint API, non testato contro
    un'istanza reale: se il tuo tracker usa un path diverso, va adattato)."""
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


def _group_view(group: list[MatchReview], torrent_client: TorrentClient | None) -> dict:
    primary = group[0]
    candidate = primary.candidate
    media_item = candidate.media_item
    disk = media_item.media_path.disk
    seed_job = group_seed_job(group)

    file_list = json.loads(candidate.file_list_json) if candidate.file_list_json else []
    video_files = [f for f in file_list if _is_video(f)]
    is_pack = len(video_files) > 1

    if is_pack:
        media_path_display = f"{media_item.media_path.relative_path} · {len(group)} episodi"
    else:
        media_path_display = media_item.file_path

    status = group_status(group)
    decided_at = max((r.decided_at for r in group if r.decided_at is not None), default=None)

    return {
        "candidate": candidate,
        "disk": disk,
        "is_pack": is_pack,
        "episode_count": len(group),
        "media_path_display": media_path_display,
        "torrent_path_display": seed_job.hardlink_path if seed_job else None,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "seed_job": seed_job,
        "decided_at": decided_at,
        "tracker_url": _tracker_url(candidate),
        "client_url": _client_url(torrent_client, seed_job.info_hash if seed_job else None),
        "tmdb_url": _tmdb_url(media_item),
    }


@router.get("/library")
def library_page(request: Request, status: str = "all", session: Session = Depends(get_session)):
    if status not in STATUS_FILTERS:
        status = "all"
    groups = list_library_groups(session, status=status)
    torrent_client = session.query(TorrentClient).filter_by(enabled=True).first()
    views = [_group_view(g, torrent_client) for g in groups]
    views.sort(key=lambda v: v["decided_at"] or v["candidate"].created_at, reverse=True)
    return templates.TemplateResponse(
        request,
        "library.html",
        {
            "views": views,
            "status": status,
            "status_labels": STATUS_LABELS,
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
