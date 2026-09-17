"""Pagina web della coda di revisione (form pieni, redirect dopo submit —
vedi CLAUDE.md: niente dipendenza da una libreria JS esterna).

Nessuna esecuzione (hardlink+seed) senza conferma umana esplicita, nemmeno
per i match che il sistema giudica affidabili (status auto_approved) —
vedi app/review.py. Mostra anche le esecuzioni fallite in precedenza, con
retry singolo o in blocco."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import review as review_service
from app.deps import get_session
from app.executor import ExecutionError
from app.models import MatchReview, RunLog, SeedJob
from app.web.templates import templates

router = APIRouter()


@router.get("/reviews")
def reviews_page(request: Request, session: Session = Depends(get_session)):
    reviews = review_service.list_ready_for_review(session)
    reviews.sort(key=lambda r: r.candidate.confidence, reverse=True)
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
        {"reviews": reviews, "failed_seed_jobs": failed_seed_jobs, "last_run": last_run},
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
