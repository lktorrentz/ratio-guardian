"""Orchestrazione dell'intero flusso: scan -> match -> auto-seed -> run_log.

Vedi docs/SPEC.md sezione 11: import massivo e run schedulato condividono
lo stesso motore, cambia solo il trigger (run_type) e il volume atteso.
Ogni run produce una riga in run_log con i contatori aggregati.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.adapter_factory import build_media_resolver, build_torrent_client_adapter, build_tracker_adapter
from app.adapters.media_resolver.base import MediaResolverAdapter
from app.adapters.torrent_client.base import TorrentClientAdapter
from app.adapters.tracker.base import TrackerAdapter
from app.executor import ExecutionError, execute_candidate
from app.matching import run_matching
from app.models import Candidate, MatchReview, RunLog, Tracker, TorrentClient
from app.scanner import count_enabled_video_files, scan_all_enabled

logger = logging.getLogger(__name__)

# Review pronte per l'esecuzione: sia quelle auto-approvate sopra soglia
# sia quelle approvate manualmente dalla coda di revisione.
_EXECUTABLE_STATUSES = ("auto_approved", "approved")


def run_pipeline(
    session: Session,
    run_type: str,
    tracker_row: Tracker,
    tracker_adapter: TrackerAdapter,
    media_resolver: MediaResolverAdapter,
    torrent_client_adapter: TorrentClientAdapter | None = None,
) -> RunLog:
    run_log = RunLog(run_type=run_type, started_at=datetime.now(timezone.utc))
    session.add(run_log)
    session.commit()
    run_log_id = run_log.id  # letto prima di un eventuale rollback più sotto

    errors = 0
    try:
        run_log.items_total = count_enabled_video_files(session)
        session.commit()

        def _on_file_scanned() -> None:
            run_log.items_scanned = (run_log.items_scanned or 0) + 1
            session.commit()

        scan_totals = scan_all_enabled(session, media_resolver, on_file_scanned=_on_file_scanned)
        match_totals = run_matching(session, tracker_row, tracker_adapter)

        auto_seeded = 0
        if torrent_client_adapter is not None:
            auto_seeded, exec_errors = _execute_pending(session, torrent_client_adapter)
            errors += exec_errors

        run_log.items_scanned = scan_totals["scanned"]
        run_log.matches_found = match_totals["candidates"]
        run_log.auto_seeded = auto_seeded
        run_log.pending_review = match_totals["pending_review"]
        run_log.errors = errors
    except Exception:
        # La sessione può essere in stato "rollback pending" dopo un
        # fallimento di flush (es. un IntegrityError durante lo scan):
        # senza rollback qui, anche solo leggere run_log.errors sotto
        # solleverebbe PendingRollbackError, mascherando l'errore vero.
        session.rollback()
        logger.exception("Run %s fallita", run_log_id)
        run_log = session.get(RunLog, run_log_id)
        run_log.errors = errors + 1
        raise
    finally:
        run_log.finished_at = datetime.now(timezone.utc)
        session.commit()

    return run_log


def _execute_pending(session: Session, torrent_client_adapter: TorrentClientAdapter) -> tuple[int, int]:
    """Esegue hardlink+seed per ogni review eseguibile il cui candidate non
    ha ancora un seed_job (non riprocessa quelle già gestite)."""
    reviews = (
        session.query(MatchReview)
        .join(Candidate)
        .filter(MatchReview.status.in_(_EXECUTABLE_STATUSES))
        .all()
    )
    seeded = 0
    errors = 0
    for review in reviews:
        if review.candidate.seed_jobs:
            continue
        try:
            job = execute_candidate(session, review.candidate, torrent_client_adapter)
        except ExecutionError:
            logger.exception("Esecuzione fallita per candidate %s", review.candidate.id)
            errors += 1
            continue
        if job is None:
            continue  # già in seeding, nessun lavoro necessario
        if job.final_status == "failed":
            errors += 1
        else:
            seeded += 1
    return seeded, errors


def get_current_run(session: Session) -> RunLog | None:
    """Il run in corso (started_at impostato, finished_at ancora nullo),
    se c'è. Usato sia per lo stato live sia per evitare run sovrapposti."""
    return session.query(RunLog).filter(RunLog.finished_at.is_(None)).order_by(RunLog.id.desc()).first()


def build_and_run_pipeline(session: Session, run_type: str) -> RunLog | None:
    """Trova tracker/torrent_client abilitati, costruisce gli adapter dal DB
    e lancia run_pipeline. Ritorna None (nessun run_log creato) se manca la
    configurazione minima — mai un errore fatale per una config incompleta."""
    tracker_row = session.query(Tracker).filter_by(enabled=True).first()
    if tracker_row is None:
        logger.info("Nessun tracker abilitato configurato: run saltato")
        return None

    try:
        tracker_adapter = build_tracker_adapter(tracker_row)
        media_resolver = build_media_resolver(session)
    except ValueError as exc:
        logger.warning("Run saltato, configurazione incompleta: %s", exc)
        return None

    torrent_client_adapter = None
    torrent_client_row = session.query(TorrentClient).filter_by(enabled=True).first()
    if torrent_client_row is not None:
        try:
            torrent_client_adapter = build_torrent_client_adapter(torrent_client_row)
        except ValueError as exc:
            logger.warning("Client torrent non utilizzabile: %s", exc)

    return run_pipeline(session, run_type, tracker_row, tracker_adapter, media_resolver, torrent_client_adapter)
