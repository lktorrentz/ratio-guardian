"""Dashboard: contatori principali, stato live del run in corso, e
trigger manuale della pipeline (asincrono - non blocca la pagina)."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import exists
from sqlalchemy.orm import Session

from app import scheduler as scheduler_module
from app.deps import get_session
from app.models import Candidate, Disk, MatchReview, MediaItem, RunLog, SeedJob, Tracker
from app.pipeline import get_current_run
from app.web.templates import templates

router = APIRouter()


@router.get("/")
def dashboard_page(request: Request, session: Session = Depends(get_session)):
    total_media_items = session.query(MediaItem).count()
    unresolved_count = session.query(MediaItem).filter(MediaItem.tmdb_id.is_(None)).count()
    # risolto (tmdb_id noto) ma nessun candidate col tracker ha confidence > 0
    no_match_count = (
        session.query(MediaItem)
        .filter(MediaItem.tmdb_id.isnot(None))
        .filter(
            ~exists().where(Candidate.media_item_id == MediaItem.id).where(Candidate.confidence > 0)
        )
        .count()
    )
    linked_count = (
        session.query(MediaItem.id)
        .join(Candidate, Candidate.media_item_id == MediaItem.id)
        .join(SeedJob, SeedJob.candidate_id == Candidate.id)
        .filter(SeedJob.final_status == "seeding")
        .distinct()
        .count()
    )

    context = {
        "pending_count": session.query(MatchReview).filter_by(status="pending").count(),
        "disk_count": session.query(Disk).count(),
        "last_run": session.query(RunLog).order_by(RunLog.id.desc()).first(),
        "current_run": get_current_run(session),
        "total_media_items": total_media_items,
        "linked_count": linked_count,
        "unresolved_count": unresolved_count,
        "no_match_count": no_match_count,
        "error": request.query_params.get("error"),
    }
    return templates.TemplateResponse(request, "dashboard.html", context)


@router.post("/run-now")
def run_now_page(request: Request, session: Session = Depends(get_session)):
    if session.query(Tracker).filter_by(enabled=True).first() is None:
        return RedirectResponse(
            url="/?error=Configurazione+incompleta+(serve+un+tracker+abilitato+e+la+TMDB+API+key)",
            status_code=303,
        )
    started = scheduler_module.run_now(request.app.state.scheduler, request.app.state.session_factory, "manual")
    if not started:
        return RedirectResponse(url="/?error=Un+run+è+già+in+corso", status_code=303)
    return RedirectResponse(url="/", status_code=303)
