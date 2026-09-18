"""Pagina web della coda di revisione (form pieni, redirect dopo submit —
vedi CLAUDE.md: niente dipendenza da una libreria JS esterna).

Nessuna esecuzione (hardlink+seed) senza conferma umana esplicita, nemmeno
per i match che il sistema giudica affidabili (status auto_approved) —
vedi app/review.py. Mostra anche le esecuzioni fallite in precedenza, con
retry singolo o in blocco."""

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import review as review_service
from app.deps import get_session
from app.executor import ExecutionError
from app.models import Disk, MatchReview, MediaPath, RunLog, SeedJob
from app.web.templates import templates

router = APIRouter()


def _group_reviews(reviews: list[MatchReview]) -> list[list[MatchReview]]:
    """Raggruppa per (tracker_id, torrent_id_remote): un season pack che
    copre più episodi orfani produce una review per episodio (stesso
    torrent, candidate_id diverso — vedi app/matching.py), ma è UNA sola
    decisione di approvazione/rifiuto (vedi app/review.py::_group_siblings)
    — mai mostrata come N righe separate azionabili singolarmente, che
    farebbe pensare a N decisioni indipendenti quando in realtà approvarne
    una crea comunque l'hardlink dell'intero pack. Un match a file singolo
    resta semplicemente un gruppo da 1, nessun trattamento speciale."""
    groups: dict[tuple[int, str], list[MatchReview]] = {}
    order: list[tuple[int, str]] = []
    for r in reviews:
        key = (r.candidate.tracker_id, r.candidate.torrent_id_remote)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)
    return [groups[key] for key in order]


def _review_group_view(group: list[MatchReview]) -> dict:
    """Espande un gruppo di review (stesso torrent, vedi _group_reviews)
    con quello che serve a capire, a colpo d'occhio, COSA farà davvero
    un'approvazione — richiesto esplicitamente dopo che non era chiaro
    se/dove sarebbe stato creato un nuovo hardlink. Per un pack che copre
    più episodi, l'azione (approve/reject) sull'id della prima review del
    gruppo si propaga a tutte le altre (vedi app/review.py)."""
    primary = group[0]
    candidate = primary.candidate
    media_items = [r.candidate.media_item for r in group]
    media_path = media_items[0].media_path
    disk = media_path.disk
    # target_path riflette dove finirà il NUOVO hardlink (sottocartella
    # per-libreria se configurata, vedi MediaPath.effective_new_torrent_rel_path)
    # — non va confuso con disk.torrents_rel_path, che è invece dove si
    # cerca "già in seeding" (sempre l'intera cartella torrent del disco).
    new_torrent_rel_path = media_path.effective_new_torrent_rel_path
    file_list = json.loads(candidate.file_list_json) if candidate.file_list_json else []

    target_path = None
    if new_torrent_rel_path:
        if candidate.folder:
            target_path = f"/{new_torrent_rel_path}/{candidate.folder}/  ({len(file_list)} file)"
        elif file_list:
            target_path = f"/{new_torrent_rel_path}/{file_list[0]}"

    # Ogni review del gruppo ha la propria confidence (calcolata sul
    # confronto per-episodio dentro il pack, non su un unico valore
    # condiviso — vedi app/matching.py::_score) e il proprio stato
    # auto_approved/pending: mostriamo il caso peggiore, mai il migliore,
    # così il badge non promette più di quanto l'evidenza più debole nel
    # gruppo giustifichi.
    confidences = [r.candidate.confidence for r in group]
    all_auto_approved = all(r.status == "auto_approved" for r in group)

    return {
        "primary_review_id": primary.id,
        "candidate": candidate,
        "is_pack": len(group) > 1,
        "file_paths": [mi.file_path for mi in media_items],
        "confidence": min(confidences),
        "all_auto_approved": all_auto_approved,
        "ambiguity_reasons": sorted({r.candidate.ambiguity_reason for r in group if r.candidate.ambiguity_reason}),
        "target_path": target_path,
        "torrents_configured": bool(disk.torrents_rel_path),
        "max_nlink": max((mi.nlink or 0) for mi in media_items),
        "already_linked_elsewhere": any((mi.nlink or 0) > 1 for mi in media_items),
    }


def _disks_missing_torrents_path(session: Session) -> list[Disk]:
    """Dischi con almeno una media path abilitata ma senza torrents_rel_path
    configurato: su questi il controllo "già in seeding" non può funzionare
    affatto (indice sempre vuoto), quindi anche file già a posto finiscono
    in coda — la causa più probabile se la coda sembra piena di file che
    "dovrebbero già esistere"."""
    return (
        session.query(Disk)
        .join(MediaPath)
        .filter(MediaPath.enabled.is_(True))
        .filter(Disk.torrents_rel_path.is_(None))
        .distinct()
        .all()
    )


@router.get("/reviews")
def reviews_page(request: Request, session: Session = Depends(get_session)):
    reviews = review_service.list_ready_for_review(session)
    groups = _group_reviews(reviews)
    groups.sort(key=lambda g: min(r.candidate.confidence for r in g), reverse=True)
    review_views = [_review_group_view(g) for g in groups]
    failed_seed_jobs = review_service.list_failed_seed_jobs(session)
    last_run = (
        session.query(RunLog)
        .filter(RunLog.finished_at.isnot(None))
        .order_by(RunLog.id.desc())
        .first()
    )
    return templates.TemplateResponse(
        request,
        "reviews.html",
        {
            "review_views": review_views,
            "failed_seed_jobs": failed_seed_jobs,
            "last_run": last_run,
            "disks_missing_torrents_path": _disks_missing_torrents_path(session),
        },
    )


@router.post("/reviews/{review_id}/approve")
def approve_review_page(review_id: int, session: Session = Depends(get_session)):
    review = session.get(MatchReview, review_id)
    if review is not None and review.status in review_service.READY_FOR_DECISION_STATUSES:
        review_service.approve(session, review)
    return RedirectResponse(url="/reviews", status_code=303)


@router.post("/reviews/{review_id}/reject")
def reject_review_page(review_id: int, session: Session = Depends(get_session)):
    review = session.get(MatchReview, review_id)
    if review is not None and review.status in review_service.READY_FOR_DECISION_STATUSES:
        review_service.reject(session, review)
    return RedirectResponse(url="/reviews", status_code=303)


@router.post("/reviews/approve-all")
def approve_all_reviews_page(session: Session = Depends(get_session)):
    review_service.approve_all(session)
    return RedirectResponse(url="/reviews", status_code=303)


@router.post("/reviews/failed/{seed_job_id}/retry")
def retry_failed_seed_job_page(seed_job_id: int, session: Session = Depends(get_session)):
    seed_job = session.get(SeedJob, seed_job_id)
    if seed_job is not None and seed_job.final_status == "failed":
        try:
            review_service.retry_failed(session, seed_job)
        except ExecutionError:
            pass  # resta failed con il nuovo error_message, visibile nella pagina
    return RedirectResponse(url="/reviews", status_code=303)


@router.post("/reviews/failed/retry-all")
def retry_all_failed_seed_jobs_page(session: Session = Depends(get_session)):
    review_service.retry_all_failed(session)
    return RedirectResponse(url="/reviews", status_code=303)
