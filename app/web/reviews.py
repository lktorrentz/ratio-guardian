"""Pagina web della coda di revisione (form pieni, redirect dopo submit —
vedi CLAUDE.md: niente dipendenza da una libreria JS esterna).

Nessuna esecuzione (hardlink+seed) senza conferma umana esplicita, nemmeno
per i match che il sistema giudica affidabili (status auto_approved) —
vedi app/review.py."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import review as review_service
from app.deps import get_session
from app.models import MatchReview, RunLog
from app.web.templates import templates

router = APIRouter()


@router.get("/reviews")
def reviews_page(request: Request, session: Session = Depends(get_session)):
    reviews = review_service.list_ready_for_review(session)
    reviews.sort(key=lambda r: r.candidate.confidence, reverse=True)
    last_run = (
        session.query(RunLog)
        .filter(RunLog.finished_at.isnot(None))
        .order_by(RunLog.id.desc())
        .first()
    )
    return templates.TemplateResponse(request, "reviews.html", {"reviews": reviews, "last_run": last_run})


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
