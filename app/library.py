"""Libreria: vista completa di tutti i file scansionati (media_item), non
solo quelli passati per una decisione — a differenza della coda di
revisione (app/review.py, solo pending/auto_approved ancora da decidere),
qui c'è tutto: chi è già collegato/seeding correttamente (anche se non è
mai stato orfano), e chi è orfano con lo stato del suo tentativo più
recente (in attesa, fallito, rifiutato, o senza alcun candidato trovato).

Raggruppata per torrent quando esiste una review/candidate associata
(stesso criterio di app/web/reviews.py: un pack è una riga sola) — un
file senza alcuno storico di match (mai orfano, o mai matchato) resta
semplicemente un gruppo da 1."""

from sqlalchemy.orm import Session, selectinload

from app.models import Candidate, MatchReview, MediaItem, MediaPath
from app.seeding_index import build_seeding_index

# 'seeding': collegato correttamente in questo momento (nlink>1 e trovato
#   nell'indice della cartella torrent del disco) — indipendentemente dal
#   fatto che sia mai stato orfano.
# 'pending' | 'in_progress' | 'failed' | 'rejected': orfano, con lo stato
#   del tentativo di match/esecuzione più recente.
# 'unmatched': orfano senza alcun candidato trovato finora (mai matchato,
#   o nessun candidate con confidence > 0).
# 'unknown': orfano, review approvata ma mai eseguita (es. nessun client
#   torrent configurato al momento dell'approvazione) — mai spacciato per
#   un esito reale.
STATUSES = ("seeding", "pending", "in_progress", "failed", "rejected", "unmatched", "unknown")
ORPHAN_STATUSES = ("pending", "in_progress", "failed", "rejected", "unmatched", "unknown")

Entry = tuple[MediaItem, str, MatchReview | None]


def _latest_review(media_item: MediaItem) -> MatchReview | None:
    """L'ultima review (per id) tra tutti i candidate mai proposti per
    questo file — le precedenti vengono marcate 'rejected' da
    supersede_reviews_for_media_item ogni volta che il file viene
    rimatchato o trovato già in seeding, quindi la più recente rappresenta
    sempre il verdetto attuale."""
    reviews = [r for c in media_item.candidates for r in c.match_reviews]
    return max(reviews, key=lambda r: r.id, default=None)


def media_item_status(media_item: MediaItem, seeding_index_cache: dict[int, set[tuple[int, int]]]) -> tuple[str, MatchReview | None]:
    disk = media_item.media_path.disk
    is_linked = False
    if media_item.nlink and media_item.nlink > 1 and media_item.st_dev is not None and media_item.inode is not None:
        if disk.id not in seeding_index_cache:
            seeding_index_cache[disk.id] = build_seeding_index(disk.root_path, disk.torrents_rel_path)
        is_linked = (media_item.st_dev, media_item.inode) in seeding_index_cache[disk.id]

    review = _latest_review(media_item)

    if is_linked:
        return "seeding", review
    if review is None:
        return "unmatched", None
    if review.status == "rejected":
        return "rejected", review
    if review.status in ("pending", "auto_approved"):
        return "pending", review

    # approved
    seed_job = review.candidate.seed_jobs[0] if review.candidate.seed_jobs else None
    if seed_job is None:
        return "unknown", review  # approvato ma mai eseguito (es. nessun client configurato all'epoca)
    if seed_job.final_status in ("failed", "rolled_back"):
        return "failed", review
    if seed_job.final_status == "in_progress":
        return "in_progress", review
    return "seeding", review  # seed_job dice seeding ma lo scan/indice non l'ha (ancora) rilevato


def group_entries(entries: list[Entry]) -> list[list[Entry]]:
    """Raggruppa per (tracker_id, torrent_id_remote) quando esiste una
    review — un pack copre più episodi orfani con lo stesso torrent, va
    mostrato come una riga sola (vedi app/web/reviews.py::_group_reviews,
    stesso criterio). Un file senza alcuna review resta un gruppo da 1."""
    groups: dict[object, list[Entry]] = {}
    order: list[object] = []
    for entry in entries:
        _, _, review = entry
        key = (review.candidate.tracker_id, review.candidate.torrent_id_remote) if review is not None else id(entry[0])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(entry)
    return [groups[k] for k in order]


def list_library_groups(session: Session, status: str = "all") -> list[list[Entry]]:
    media_items = (
        session.query(MediaItem)
        .join(MediaPath)
        .filter(MediaPath.enabled.is_(True))
        .options(
            selectinload(MediaItem.media_path).selectinload(MediaPath.disk),
            selectinload(MediaItem.candidates).selectinload(Candidate.match_reviews),
            selectinload(MediaItem.candidates).selectinload(Candidate.seed_jobs),
            selectinload(MediaItem.candidates).selectinload(Candidate.tracker),
        )
        .all()
    )
    seeding_index_cache: dict[int, set[tuple[int, int]]] = {}
    entries: list[Entry] = [(mi, *media_item_status(mi, seeding_index_cache)) for mi in media_items]
    groups = group_entries(entries)

    if status and status != "all":
        if status == "orphan":
            groups = [g for g in groups if g[0][1] in ORPHAN_STATUSES]
        else:
            groups = [g for g in groups if g[0][1] == status]
    return groups
