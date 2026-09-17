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

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.adapter_factory import build_torrent_client_adapter
from app.executor import ExecutionError, execute_candidate, retry_seed_job
from app.models import Candidate, MatchReview, SeedJob, TorrentClient
from app.settings_repo import get_setting

logger = logging.getLogger(__name__)

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
    """Approva e prova subito l'esecuzione (hardlink+seed) se un client
    torrent è configurato. Un fallimento dell'esecuzione non annulla
    l'approvazione: resta approved con il seed_job in stato failed. NOTA:
    non viene ritentata automaticamente qui — un candidate con un seed_job
    (anche fallito) non ricompare più in list_ready_for_review(); compare
    invece in list_failed_seed_jobs(), con retry_failed() per ritentarlo."""
    review.status = "approved"
    review.decided_by = decided_by
    review.decided_at = datetime.now(timezone.utc)
    session.commit()
    _try_execute(session, review)
    return review


def _build_torrent_client_adapter_or_none(session: Session):
    torrent_client_row = session.query(TorrentClient).filter_by(enabled=True).first()
    if torrent_client_row is None:
        logger.info("Nessun client torrent configurato: esecuzione rimandata")
        return None
    return build_torrent_client_adapter(torrent_client_row)


def _try_execute(session: Session, review: MatchReview) -> None:
    adapter = _build_torrent_client_adapter_or_none(session)
    if adapter is None:
        return
    try:
        execute_candidate(session, review.candidate, adapter)
    except ExecutionError:
        logger.exception(
            "Esecuzione immediata fallita per candidate %s (visibile tra le esecuzioni fallite, ritenta da lì)",
            review.candidate.id,
        )
    except Exception:
        logger.exception("Errore inatteso nell'esecuzione immediata per candidate %s", review.candidate.id)


def reject(session: Session, review: MatchReview, decided_by: str = "user") -> MatchReview:
    review.status = "rejected"
    review.decided_by = decided_by
    review.decided_at = datetime.now(timezone.utc)
    session.commit()
    return review


# Richiesta esplicita dell'utente: nessuna esecuzione (hardlink+seed) senza
# conferma umana, nemmeno per i match che il sistema giudica affidabili
# (auto_approved) — quella classificazione resta solo un'indicazione nella
# UI ("alta confidence" vs "da verificare"), mai un lasciapassare automatico.
READY_FOR_DECISION_STATUSES = ("pending", "auto_approved")


def list_ready_for_review(session: Session) -> list[MatchReview]:
    """match_review in attesa di una decisione (pending o auto_approved) il
    cui candidate non ha ancora un seed_job — esclude quelle già eseguite
    (con successo o meno) in un tentativo precedente."""
    reviews = (
        session.query(MatchReview)
        .join(Candidate)
        .filter(MatchReview.status.in_(READY_FOR_DECISION_STATUSES))
        .all()
    )
    return [r for r in reviews if not r.candidate.seed_jobs]


def approve_all(session: Session, decided_by: str = "user") -> int:
    """Approva ed esegue ogni review pronta (vedi list_ready_for_review).
    Ritorna quante ne ha processate. Un fallimento su una non blocca le
    altre — ciascuna resta con il proprio esito (visibile su seed_job)."""
    reviews = list_ready_for_review(session)
    for review in reviews:
        approve(session, review, decided_by=decided_by)
    return len(reviews)


def list_failed_seed_jobs(session: Session) -> list[SeedJob]:
    return session.query(SeedJob).filter_by(final_status="failed").all()


def retry_failed(session: Session, seed_job: SeedJob) -> SeedJob:
    """Ritenta un'esecuzione fallita, riprendendo dal punto giusto (vedi
    app.executor.retry_seed_job) invece di ripartire ciecamente da zero."""
    adapter = _build_torrent_client_adapter_or_none(session)
    if adapter is None:
        raise ExecutionError("Nessun client torrent configurato")
    return retry_seed_job(session, seed_job, adapter)


def retry_all_failed(session: Session) -> dict[str, int]:
    """Ritenta ogni esecuzione fallita. Ritorna quante sono riuscite/fallite
    di nuovo — un fallimento su una non blocca le altre."""
    seed_jobs = list_failed_seed_jobs(session)
    succeeded = 0
    failed = 0
    for seed_job in seed_jobs:
        try:
            result = retry_failed(session, seed_job)
        except ExecutionError:
            logger.exception("Retry fallito per seed_job %s", seed_job.id)
            failed += 1
            continue
        if result.final_status == "failed":
            failed += 1
        else:
            succeeded += 1
    return {"succeeded": succeeded, "failed": failed}
