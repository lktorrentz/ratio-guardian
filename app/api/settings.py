"""API per le impostazioni dinamiche editabili da UI (docs/SPEC.md sez. 4)."""

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import scheduler as scheduler_module
from app.deps import get_session
from app.review import DEFAULT_CONFIDENCE_THRESHOLD
from app.settings_repo import get_setting, set_setting

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ScheduleSettings(BaseModel):
    schedule_cron: str | None = None


class GeneralSettings(BaseModel):
    confidence_threshold_auto: float
    has_tmdb_api_key: bool


class GeneralSettingsUpdate(BaseModel):
    confidence_threshold_auto: float | None = None
    tmdb_api_key: str | None = None


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


@router.get("/general", response_model=GeneralSettings)
def get_general_settings(session: Session = Depends(get_session)):
    return GeneralSettings(
        confidence_threshold_auto=float(
            get_setting(session, "confidence_threshold_auto", str(DEFAULT_CONFIDENCE_THRESHOLD))
        ),
        has_tmdb_api_key=bool(get_setting(session, "tmdb_api_key")),
    )


@router.put("/general", response_model=GeneralSettings)
def put_general_settings(body: GeneralSettingsUpdate, session: Session = Depends(get_session)):
    if body.confidence_threshold_auto is not None:
        if not 0.0 <= body.confidence_threshold_auto <= 1.0:
            raise HTTPException(status_code=400, detail="confidence_threshold_auto deve essere tra 0.0 e 1.0")
        set_setting(session, "confidence_threshold_auto", str(body.confidence_threshold_auto))
    if body.tmdb_api_key:
        set_setting(session, "tmdb_api_key", body.tmdb_api_key)
    return get_general_settings(session)
