"""API per lo storico dei run (run_log) e il trigger manuale della
pipeline (import massivo / run manuale). Vedi docs/SPEC.md sezione 11.

Il trigger è asincrono (job in background sullo scheduler già esistente,
mai bloccante): la richiesta torna subito, il progresso si segue via
GET /api/runs/current — necessario perché lo stato live abbia senso
mentre si naviga altrove nella webapp."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import scheduler as scheduler_module
from app.deps import get_session
from app.models import RunLog
from app.pipeline import get_current_run

router = APIRouter(prefix="/api/runs", tags=["runs"])


class RunLogResponse(BaseModel):
    id: int
    run_type: str
    started_at: str
    finished_at: str | None
    items_total: int | None
    items_scanned: int
    matches_found: int
    auto_seeded: int
    pending_review: int
    errors: int

    @classmethod
    def from_model(cls, run_log: RunLog) -> "RunLogResponse":
        return cls(
            id=run_log.id,
            run_type=run_log.run_type,
            started_at=run_log.started_at.isoformat(),
            finished_at=run_log.finished_at.isoformat() if run_log.finished_at else None,
            items_total=run_log.items_total,
            items_scanned=run_log.items_scanned or 0,
            matches_found=run_log.matches_found or 0,
            auto_seeded=run_log.auto_seeded or 0,
            pending_review=run_log.pending_review or 0,
            errors=run_log.errors or 0,
        )


@router.get("", response_model=list[RunLogResponse])
def list_runs(limit: int = 50, session: Session = Depends(get_session)):
    runs = session.query(RunLog).order_by(RunLog.id.desc()).limit(limit).all()
    return [RunLogResponse.from_model(r) for r in runs]


@router.get("/current", response_model=RunLogResponse | None)
def current_run(session: Session = Depends(get_session)):
    run_log = get_current_run(session)
    return RunLogResponse.from_model(run_log) if run_log is not None else None


@router.post("/trigger", status_code=202)
def trigger_run(
    request: Request,
    run_type: Literal["manual", "bulk_import"] = "manual",
    session: Session = Depends(get_session),
):
    from app.models import Tracker

    if session.query(Tracker).filter_by(enabled=True).first() is None:
        raise HTTPException(
            status_code=409,
            detail="Configurazione incompleta (serve almeno un tracker abilitato e tmdb_api_key)",
        )

    started = scheduler_module.run_now(request.app.state.scheduler, request.app.state.session_factory, run_type)
    if not started:
        raise HTTPException(status_code=409, detail="Un run è già in corso")
    return {"status": "started"}
