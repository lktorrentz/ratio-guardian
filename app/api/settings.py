"""API per le impostazioni dinamiche editabili da UI (docs/SPEC.md sez. 4).

Per ora: schedulazione (schedule_cron). Le altre (soglia confidence,
tmdb_api_key, ecc.) si leggono/scrivono già via app.settings_repo — un
endpoint dedicato per quelle si aggiunge quando servirà una UI apposita.
"""

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import scheduler as scheduler_module
from app.deps import get_session
from app.settings_repo import get_setting, set_setting

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ScheduleSettings(BaseModel):
    schedule_cron: str | None = None


@router.get("/schedule", response_model=ScheduleSettings)
def get_schedule(session: Session = Depends(get_session)):
    return ScheduleSettings(schedule_cron=get_setting(session, "schedule_cron"))


@router.put("/schedule", response_model=ScheduleSettings)
def put_schedule(body: ScheduleSettings, request: Request, session: Session = Depends(get_session)):
    cron_expr = body.schedule_cron or None
    if cron_expr:
        try:
            CronTrigger.from_crontab(cron_expr)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Espressione cron non valida: {exc}") from exc

    set_setting(session, "schedule_cron", cron_expr or "")
    scheduler_module.update_schedule(request.app.state.scheduler, request.app.state.session_factory, cron_expr)
    return ScheduleSettings(schedule_cron=cron_expr)
