"""Pagina web dello storico run (run_log). Vedi docs/SPEC.md sezione 11."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.deps import get_session
from app.models import RunLog
from app.web.templates import templates

router = APIRouter()


@router.get("/runs")
def runs_page(request: Request, session: Session = Depends(get_session)):
    runs = session.query(RunLog).order_by(RunLog.id.desc()).limit(50).all()
    return templates.TemplateResponse(request, "runs.html", {"runs": runs})
