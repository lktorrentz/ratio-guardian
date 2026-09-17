"""Dashboard: contatori principali + trigger manuale della pipeline."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.deps import get_session
from app.models import Disk, MatchReview, RunLog
from app.pipeline import build_and_run_pipeline
from app.web.templates import templates

router = APIRouter()


@router.get("/")
def dashboard_page(request: Request, session: Session = Depends(get_session)):
    context = {
        "pending_count": session.query(MatchReview).filter_by(status="pending").count(),
        "disk_count": session.query(Disk).count(),
        "last_run": session.query(RunLog).order_by(RunLog.id.desc()).first(),
        "error": request.query_params.get("error"),
    }
    return templates.TemplateResponse(request, "dashboard.html", context)


@router.post("/run-now")
def run_now_page(session: Session = Depends(get_session)):
    run_log = build_and_run_pipeline(session, "manual")
    if run_log is None:
        return RedirectResponse(
            url="/?error=Configurazione+incompleta+(serve+un+tracker+abilitato+e+la+TMDB+API+key)",
            status_code=303,
        )
    return RedirectResponse(url="/", status_code=303)
