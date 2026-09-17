"""Scheduler in-process (APScheduler) per i run periodici.

Vedi docs/SPEC.md sezione 11 e CLAUDE.md: cron configurabile da UI, niente
cron esterno di sistema. `schedule_cron` vive in app_settings — se assente
o vuoto, lo scheduler non pianifica nulla finché l'utente non lo configura.
Cambiare la schedulazione da UI la applica subito, mai richiede un restart.
"""

import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.pipeline import build_and_run_pipeline
from app.settings_repo import get_setting

logger = logging.getLogger(__name__)

JOB_ID = "scheduled_run"

# Un solo processo Python (container singolo, un solo programma supervisord):
# basta un lock in-process, niente lock a livello DB. Acquisito DENTRO il job
# (non prima di accodarlo) perché è l'unico punto atomico — un controllo
# "c'è già un run?" fatto guardando run_log PRIMA di accodare il job avrebbe
# una finestra di race (il job non ha ancora scritto nulla quando un secondo
# trigger arriva nel frattempo): due scan concorrenti sugli stessi file
# violano il vincolo UNIQUE di media_item, verificato riproducendolo.
_run_lock = threading.Lock()


def _run_job_locked(session_factory, run_type: str) -> None:
    if not _run_lock.acquire(blocking=False):
        logger.info("Run %s saltato: un altro run è già in corso", run_type)
        return
    try:
        with session_factory() as session:
            build_and_run_pipeline(session, run_type)
    finally:
        _run_lock.release()


def _add_job(scheduler: BackgroundScheduler, session_factory, cron_expr: str) -> None:
    scheduler.add_job(
        _run_job_locked,
        trigger=CronTrigger.from_crontab(cron_expr),
        args=[session_factory, "scheduled"],
        id=JOB_ID,
        replace_existing=True,
    )


def run_now(scheduler: BackgroundScheduler, session_factory, run_type: str) -> bool:
    """Accoda un run immediato in background (mai bloccante per chi chiama
    — la pagina/API tornano subito, il progresso si segue via
    /api/runs/current). Il "False" qui è solo un fast-path per rispondere
    subito 409: la vera mutua esclusione è _run_lock dentro _run_job_locked."""
    if _run_lock.locked():
        return False
    scheduler.add_job(_run_job_locked, args=[session_factory, run_type])
    return True


def create_scheduler(session_factory) -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    with session_factory() as session:
        cron_expr = get_setting(session, "schedule_cron")
    if cron_expr:
        _add_job(scheduler, session_factory, cron_expr)
    scheduler.start()
    return scheduler


def update_schedule(scheduler: BackgroundScheduler, session_factory, cron_expr: str | None) -> None:
    if scheduler.get_job(JOB_ID) is not None:
        scheduler.remove_job(JOB_ID)
    if cron_expr:
        _add_job(scheduler, session_factory, cron_expr)
