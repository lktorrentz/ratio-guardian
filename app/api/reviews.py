"""API per la coda di revisione: lista dei match_review e decisione
(approvazione/rifiuto) manuale, singola o in blocco. Vedi docs/SPEC.md
sezione 9.

Nessuna esecuzione (hardlink+seed) senza conferma umana esplicita, nemmeno
per i match che il sistema giudica affidabili (status auto_approved) —
vedi app/review.py."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import review as review_service
from app.deps import get_session
from app.models import MatchReview

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


class ReviewResponse(BaseModel):
    id: int
    status: str
    decided_by: str | None
    decided_at: str | None
    candidate_id: int
    torrent_name: str
    confidence: float
    ambiguity_reason: str | None
    media_item_id: int
    media_item_file_path: str

    @classmethod
    def from_review(cls, review: MatchReview) -> "ReviewResponse":
        candidate = review.candidate
        return cls(
            id=review.id,
            status=review.status,
            decided_by=review.decided_by,
            decided_at=review.decided_at.isoformat() if review.decided_at else None,
            candidate_id=candidate.id,
            torrent_name=candidate.name,
            confidence=candidate.confidence,
            ambiguity_reason=candidate.ambiguity_reason,
            media_item_id=candidate.media_item_id,
            media_item_file_path=candidate.media_item.file_path,
        )


def _get_review_or_404(session: Session, review_id: int) -> MatchReview:
    review = session.get(MatchReview, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail=f"Review {review_id} non trovata")
    return review


def _require_decidable(review: MatchReview) -> None:
    if review.status not in review_service.READY_FOR_DECISION_STATUSES:
        raise HTTPException(
            status_code=409, detail=f"Review {review.id} non è decidibile (stato attuale: {review.status})"
        )


@router.get("", response_model=list[ReviewResponse])
def list_reviews(status: str | None = None, session: Session = Depends(get_session)):
    if status is not None:
        reviews = session.query(MatchReview).filter(MatchReview.status == status).all()
    else:
        reviews = review_service.list_ready_for_review(session)
    return [ReviewResponse.from_review(r) for r in reviews]


@router.post("/{review_id}/approve", response_model=ReviewResponse)
def approve_review(review_id: int, session: Session = Depends(get_session)):
    review = _get_review_or_404(session, review_id)
    _require_decidable(review)
    review_service.approve(session, review)
    return ReviewResponse.from_review(review)


@router.post("/{review_id}/reject", response_model=ReviewResponse)
def reject_review(review_id: int, session: Session = Depends(get_session)):
    review = _get_review_or_404(session, review_id)
    _require_decidable(review)
    review_service.reject(session, review)
    return ReviewResponse.from_review(review)


@router.post("/approve-all")
def approve_all_reviews(session: Session = Depends(get_session)):
    approved = review_service.approve_all(session)
    return {"approved": approved}
