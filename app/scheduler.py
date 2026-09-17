"""Scheduler in-process (APScheduler) per i run periodici.

Vedi docs/SPEC.md sezione 11 e CLAUDE.md: cron configurabile da UI, niente
cron esterno di sistema. `schedule_cron` vive in app_settings — se assente
o vuoto, lo scheduler non pianifica nulla finché l'utente non lo configura.
Cambiare la schedulazione da UI la applica subito, mai richiede un restart.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.pipeline import build_and_run_pipeline
from app.settings_repo import get_setting

logger = logging.getLogger(__name__)

JOB_ID = "scheduled_run"


def _run_scheduled_job(session_factory) -> None:
    with session_factory() as session:
        build_and_run_pipeline(session, "scheduled")


def _add_job(scheduler: BackgroundScheduler, session_factory, cron_expr: str) -> None:
    scheduler.add_job(
        _run_scheduled_job,
        trigger=CronTrigger.from_crontab(cron_expr),
        args=[session_factory],
        id=JOB_ID,
        replace_existing=True,
    )


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
