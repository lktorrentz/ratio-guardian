"""Orchestrazione dell'intero flusso: scan -> match -> auto-seed -> run_log.

Vedi docs/SPEC.md sezione 11: import massivo e run schedulato condividono
lo stesso motore, cambia solo il trigger (run_type) e il volume atteso.
Ogni run produce una riga in run_log con i contatori aggregati.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.adapter_factory import build_media_resolver, build_torrent_client_adapter, build_tracker_adapter
from app.adapters.media_resolver.base import MediaResolverAdapter
from app.adapters.torrent_client.base import TorrentClientAdapter
from app.adapters.tracker.base import TrackerAdapter
from app.executor import ExecutionError, execute_candidate
from app.matching import run_matching
from app.models import Candidate, MatchReview, MediaItem, RunLog, Tracker, TorrentClient
from app.scanner import count_enabled_video_files, scan_all_enabled

logger = logging.getLogger(__name__)

# Review pronte per l'esecuzione: sia quelle auto-approvate sopra soglia
# sia quelle approvate manualmente dalla coda di revisione.
_EXECUTABLE_STATUSES = ("auto_approved", "approved")


def close_stale_runs(session: Session) -> int:
    """Da chiamare una volta all'avvio, prima che lo scheduler parta.

    Il lock che serializza i run (app.scheduler._run_lock) è in-process:
    non sopravvive a un riavvio del container. Qualunque run_log ancora
    "in corso" (finished_at nullo) quando l'app riparte è quindi per forza
    orfano — interrotto da un riavvio/crash del processo precedente, mai
    un run realmente ancora attivo — e va chiuso come fallito invece di
    restare bloccato per sempre nello stato live (get_current_run non
    tornerebbe mai più None). Ritorna quante ne ha chiuse."""
    stale_runs = session.query(RunLog).filter(RunLog.finished_at.is_(None)).all()
    for run_log in stale_runs:
        run_log.finished_at = datetime.now(timezone.utc)
        run_log.errors = (run_log.errors or 0) + 1
    session.commit()
    if stale_runs:
        logger.warning("Chiuse %d run orfane (interrotte da un riavvio precedente)", len(stale_runs))
    return len(stale_runs)


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
        run_log.current_phase = "scanning"
        run_log.items_total = count_enabled_video_files(session)
        run_log.phase_total = run_log.items_total
        run_log.phase_done = 0
        session.commit()

        def _on_file_scanned() -> None:
            run_log.items_scanned = (run_log.items_scanned or 0) + 1
            run_log.phase_done = run_log.items_scanned
            session.commit()

        scan_totals = scan_all_enabled(session, media_resolver, on_file_scanned=_on_file_scanned)

        run_log.current_phase = "matching"
        matching_items = session.query(MediaItem).filter(MediaItem.tmdb_id.isnot(None)).all()
        run_log.phase_total = len(matching_items)
        run_log.phase_done = 0
        session.commit()

        def _on_item_matched() -> None:
            run_log.phase_done = (run_log.phase_done or 0) + 1
            session.commit()

        match_totals = run_matching(
            session, tracker_row, tracker_adapter, media_items=matching_items, on_item_matched=_on_item_matched
        )

        auto_seeded = 0
        if torrent_client_adapter is not None:
            run_log.current_phase = "executing"
            executable_reviews = _get_executable_reviews(session)
            run_log.phase_total = len(executable_reviews)
            run_log.phase_done = 0
            session.commit()

            def _on_executed() -> None:
                run_log.phase_done = (run_log.phase_done or 0) + 1
                session.commit()

            auto_seeded, exec_errors = _execute_pending(
                session, torrent_client_adapter, reviews=executable_reviews, on_executed=_on_executed
            )
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
        run_log.current_phase = None
        run_log.finished_at = datetime.now(timezone.utc)
        session.commit()

    return run_log


def _get_executable_reviews(session: Session) -> list[MatchReview]:
    return (
        session.query(MatchReview)
        .join(Candidate)
        .filter(MatchReview.status.in_(_EXECUTABLE_STATUSES))
        .all()
    )


def _execute_pending(
    session: Session,
    torrent_client_adapter: TorrentClientAdapter,
    reviews: list[MatchReview] | None = None,
    on_executed: Callable[[], None] | None = None,
) -> tuple[int, int]:
    """Esegue hardlink+seed per ogni review eseguibile il cui candidate non
    ha ancora un seed_job (non riprocessa quelle già gestite)."""
    if reviews is None:
        reviews = _get_executable_reviews(session)

    seeded = 0
    errors = 0
    for review in reviews:
        if review.candidate.seed_jobs:
            if on_executed is not None:
                on_executed()
            continue
        try:
            job = execute_candidate(session, review.candidate, torrent_client_adapter)
        except ExecutionError:
            logger.exception("Esecuzione fallita per candidate %s", review.candidate.id)
            errors += 1
            if on_executed is not None:
                on_executed()
            continue
        if job is None:
            pass  # già in seeding, nessun lavoro necessario
        elif job.final_status == "failed":
            errors += 1
        else:
            seeded += 1
        if on_executed is not None:
            on_executed()
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
