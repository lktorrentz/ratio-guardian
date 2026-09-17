"""Pagine HTML server-rendered (Jinja2, form pieni — niente JS esterno).

Vedi CLAUDE.md: web UI in Jinja2+HTMX, "niente build frontend pesante".
Per ora usiamo solo form HTML puri (redirect dopo submit): niente
dipendenza da una libreria JS esterna da vendorizzare/scaricare da CDN.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import review as review_service
from app.deps import get_session
from app.models import MatchReview

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


@router.get("/reviews")
def reviews_page(request: Request, session: Session = Depends(get_session)):
    reviews = session.query(MatchReview).filter(MatchReview.status == "pending").all()
    return templates.TemplateResponse(request, "reviews.html", {"reviews": reviews})


@router.post("/reviews/{review_id}/approve")
def approve_review_page(review_id: int, session: Session = Depends(get_session)):
    review = session.get(MatchReview, review_id)
    if review is not None and review.status == "pending":
        review_service.approve(session, review)
    return RedirectResponse(url="/reviews", status_code=303)


@router.post("/reviews/{review_id}/reject")
def reject_review_page(review_id: int, session: Session = Depends(get_session)):
    review = session.get(MatchReview, review_id)
    if review is not None and review.status == "pending":
        review_service.reject(session, review)
    return RedirectResponse(url="/reviews", status_code=303)
