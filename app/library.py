"""Storico di tutti i match già decisi (approvati/eseguiti con successo o
falliti, o rifiutati) — mai i 'pending'/'auto_approved' ancora da
decidere, quelli restano SOLO in app/review.py (coda di revisione): niente
duplicazione tra le due pagine, vedi CLAUDE.md.

Raggruppata per torrent come la coda di revisione (un pack = una riga),
qui però un solo livello: la cartella così come la riporta il tracker
(candidate.folder), senza distinguere season pack da complete pack (v1)."""

from sqlalchemy.orm import Session

from app.models import Candidate, MatchReview

LIBRARY_STATUSES = ("approved", "rejected")

# Ordine di priorità per capire lo stato "vero" di un gruppo dal seed_job
# del suo membro che l'ha davvero eseguito (solo la review "primaria" al
# momento dell'approvazione ottiene un seed_job — vedi app/review.py::approve,
# i sibling dello stesso pack vengono solo marcati approved).
_SEED_JOB_STATUS_LABELS = {
    "seeding": "seeding",
    "failed": "failed",
    "rolled_back": "failed",
    "in_progress": "in_progress",
}


def group_by_torrent(reviews: list[MatchReview]) -> list[list[MatchReview]]:
    """Stessa logica di app/web/reviews.py::_group_reviews — condivisa qui
    perché serve sia alla coda di revisione sia alla Libreria."""
    groups: dict[tuple[int, str], list[MatchReview]] = {}
    order: list[tuple[int, str]] = []
    for r in reviews:
        key = (r.candidate.tracker_id, r.candidate.torrent_id_remote)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)
    return [groups[key] for key in order]


def group_status(group: list[MatchReview]) -> str:
    """'seeding' | 'failed' | 'in_progress' | 'rejected' | 'unknown'.

    'unknown' copre il caso limite di una review approvata ma mai eseguita
    (es. nessun client torrent configurato al momento dell'approvazione) —
    mai spacciato per un esito reale."""
    if all(r.status == "rejected" for r in group):
        return "rejected"
    seed_job = next((r.candidate.seed_jobs[0] for r in group if r.candidate.seed_jobs), None)
    if seed_job is None:
        return "unknown"
    return _SEED_JOB_STATUS_LABELS.get(seed_job.final_status, "unknown")


def group_seed_job(group: list[MatchReview]):
    """Il seed_job (unico, se esiste) associato a questo gruppo — sempre
    sulla candidate della review che ha davvero eseguito, mai su un
    sibling (vedi group_status)."""
    return next((r.candidate.seed_jobs[0] for r in group if r.candidate.seed_jobs), None)


def list_library_groups(session: Session, status: str = "all") -> list[list[MatchReview]]:
    reviews = session.query(MatchReview).join(Candidate).filter(MatchReview.status.in_(LIBRARY_STATUSES)).all()
    groups = group_by_torrent(reviews)
    if status and status != "all":
        groups = [g for g in groups if group_status(g) == status]
    return groups
