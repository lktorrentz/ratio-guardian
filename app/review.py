"""Coda di revisione: crea e gestisce le righe match_review.

Vedi docs/SPEC.md sezione 9. Ogni media_item produce al massimo UNA riga
di review, sul suo candidate a confidence più alta — le alternative restano
visibili in `candidate` per audit ma non generano righe di review proprie.
Sopra soglia -> auto_approved (l'esecuzione vera e propria di hardlink+seed
è demandata all'esecutore, prossimo step della roadmap). Sotto soglia ma
con un candidato plausibile (confidence > 0) -> pending. Nessun candidato
plausibile (confidence 0.0 per tutti) -> nessuna riga di review, niente da
decidere.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Candidate, MatchReview
from app.settings_repo import get_setting

DEFAULT_CONFIDENCE_THRESHOLD = 0.95


def get_confidence_threshold(session: Session) -> float:
    return float(get_setting(session, "confidence_threshold_auto", str(DEFAULT_CONFIDENCE_THRESHOLD)))


def create_review_for_candidates(session: Session, candidates: list[Candidate]) -> MatchReview | None:
    """`candidates` deve contenere tutti i candidate relativi allo stesso
    media_item (l'output di match_media_item)."""
    if not candidates:
        return None

    best = max(candidates, key=lambda c: c.confidence)
    if best.confidence <= 0.0:
        return None

    threshold = get_confidence_threshold(session)
    if best.confidence >= threshold:
        review = MatchReview(
            candidate_id=best.id,
            status="auto_approved",
            decided_by="system",
            decided_at=datetime.now(timezone.utc),
        )
    else:
        review = MatchReview(candidate_id=best.id, status="pending")

    session.add(review)
    session.commit()
    return review


def approve(session: Session, review: MatchReview, decided_by: str = "user") -> MatchReview:
    review.status = "approved"
    review.decided_by = decided_by
    review.decided_at = datetime.now(timezone.utc)
    session.commit()
    return review


def reject(session: Session, review: MatchReview, decided_by: str = "user") -> MatchReview:
    review.status = "rejected"
    review.decided_by = decided_by
    review.decided_at = datetime.now(timezone.utc)
    session.commit()
    return review
