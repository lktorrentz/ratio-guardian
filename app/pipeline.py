"""Orchestrazione dello scan+match: scan -> match -> run_log.

Vedi docs/SPEC.md sezione 11: import massivo e run schedulato condividono
lo stesso motore, cambia solo il trigger (run_type) e il volume atteso.
Ogni run produce una riga in run_log con i contatori aggregati.

L'esecuzione (hardlink + seed) NON è più automatica a fine run — richiesta
esplicita dell'utente: prima di toccare filesystem/client torrent, ogni
match (auto-approvato dal sistema o no) aspetta una conferma umana dalla
coda di revisione (vedi app/review.py), singola o in blocco. Un run
produce quindi solo candidate/match_review, mai un seed_job."""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.adapter_factory import build_media_resolver, build_tracker_adapter
from app.adapters.media_resolver.base import MediaResolverAdapter
from app.adapters.tracker.base import TrackerAdapter
from app.matching import run_matching
from app.models import MediaItem, RunLog, Tracker
from app.review import reconcile_pending_seed_jobs
from app.scanner import count_enabled_video_files, scan_all_enabled

logger = logging.getLogger(__name__)


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
) -> RunLog:
    run_log = RunLog(run_type=run_type, started_at=datetime.now(timezone.utc))
    session.add(run_log)
    session.commit()
    run_log_id = run_log.id  # letto prima di un eventuale rollback più sotto
    logger.info("Run %s (%s) avviata", run_log_id, run_type)

    errors = 0
    try:
        run_log.current_phase = "scanning"
        run_log.items_total = count_enabled_video_files(session)
        run_log.phase_total = run_log.items_total
        run_log.phase_done = 0
        session.commit()
        logger.info("Fase scansione: %d file da esaminare", run_log.items_total)

        def _on_file_scanned() -> None:
            run_log.items_scanned = (run_log.items_scanned or 0) + 1
            run_log.phase_done = run_log.items_scanned
            session.commit()

        scan_totals = scan_all_enabled(session, media_resolver, on_file_scanned=_on_file_scanned)
        logger.info(
            "Scansione completata: %d file, %d risolti, %d non risolti, %d già in seeding",
            scan_totals["scanned"],
            scan_totals["resolved"],
            scan_totals["unresolved"],
            scan_totals["already_seeding"],
        )

        run_log.current_phase = "matching"
        matching_items = session.query(MediaItem).filter(MediaItem.tmdb_id.isnot(None)).all()
        run_log.phase_total = len(matching_items)
        run_log.phase_done = 0
        session.commit()
        logger.info("Fase matching: %d media_item da verificare sul tracker", len(matching_items))

        def _on_item_matched() -> None:
            run_log.phase_done = (run_log.phase_done or 0) + 1
            session.commit()

        match_totals = run_matching(
            session, tracker_row, tracker_adapter, media_items=matching_items, on_item_matched=_on_item_matched
        )
        logger.info(
            "Matching completato: %d candidati trovati, %d pronti per conferma (auto), "
            "%d da rivedere, %d già in seeding",
            match_totals["candidates"],
            match_totals["auto_approved"],
            match_totals["pending_review"],
            match_totals["already_seeding"],
        )

        reconcile_totals = reconcile_pending_seed_jobs(session)
        if reconcile_totals["reconciled"]:
            logger.info(
                "Ricontrollati %d seed_job in corso presso il client (%d errori)",
                reconcile_totals["reconciled"],
                reconcile_totals["errors"],
            )

        run_log.items_scanned = scan_totals["scanned"]
        run_log.matches_found = match_totals["candidates"]
        run_log.pending_review = match_totals["pending_review"] + match_totals["auto_approved"]
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
        logger.info(
            "Run %s terminata: %d in attesa di conferma per l'hardlink, errori: %d",
            run_log_id,
            run_log.pending_review or 0,
            run_log.errors or 0,
        )

    return run_log


def get_current_run(session: Session) -> RunLog | None:
    """Il run in corso (started_at impostato, finished_at ancora nullo),
    se c'è. Usato sia per lo stato live sia per evitare run sovrapposti."""
    return session.query(RunLog).filter(RunLog.finished_at.is_(None)).order_by(RunLog.id.desc()).first()


def build_and_run_pipeline(session: Session, run_type: str) -> RunLog | None:
    """Trova un tracker abilitato, costruisce gli adapter dal DB e lancia
    run_pipeline (solo scan+match, mai esecuzione — vedi sopra). Ritorna
    None (nessun run_log creato) se manca la configurazione minima — mai
    un errore fatale per una config incompleta."""
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

    return run_pipeline(session, run_type, tracker_row, tracker_adapter, media_resolver)
