"""Pagina web delle impostazioni dinamiche (docs/SPEC.md sezione 4)."""

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import scheduler as scheduler_module
from app.deps import get_session
from app.review import DEFAULT_CONFIDENCE_THRESHOLD
from app.settings_repo import get_setting, set_setting
from app.web.templates import templates

router = APIRouter(prefix="/config")


@router.get("/settings")
def settings_page(request: Request, session: Session = Depends(get_session)):
    context = {
        "confidence_threshold_auto": get_setting(
            session, "confidence_threshold_auto", str(DEFAULT_CONFIDENCE_THRESHOLD)
        ),
        "schedule_cron": get_setting(session, "schedule_cron") or "",
        "has_tmdb_api_key": bool(get_setting(session, "tmdb_api_key")),
        "error": request.query_params.get("error"),
        "saved": request.query_params.get("saved"),
    }
    return templates.TemplateResponse(request, "settings.html", context)


@router.post("/settings")
def save_settings_page(
    request: Request,
    confidence_threshold_auto: float = Form(...),
    schedule_cron: str = Form(""),
    tmdb_api_key: str = Form(""),
    session: Session = Depends(get_session),
):
    if not 0.0 <= confidence_threshold_auto <= 1.0:
        return RedirectResponse(url="/config/settings?error=Soglia+deve+essere+tra+0+e+1", status_code=303)

    cron_expr = schedule_cron.strip() or None
    if cron_expr:
        try:
            CronTrigger.from_crontab(cron_expr)
        except ValueError:
            return RedirectResponse(url="/config/settings?error=Espressione+cron+non+valida", status_code=303)

    set_setting(session, "confidence_threshold_auto", str(confidence_threshold_auto))
    set_setting(session, "schedule_cron", cron_expr or "")
    if tmdb_api_key.strip():
        set_setting(session, "tmdb_api_key", tmdb_api_key.strip())

    scheduler_module.update_schedule(request.app.state.scheduler, request.app.state.session_factory, cron_expr)
    return RedirectResponse(url="/config/settings?saved=1", status_code=303)
