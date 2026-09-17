"""Pagina web di configurazione tracker (docs/SPEC.md sezione 7).

Un solo adapter_type supportato oggi (unit3d), impostato server-side —
niente select per un'unica opzione. api_token non viene mai renderizzato
in chiaro nel template, solo un indicatore booleano.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.deps import get_session
from app.models import Tracker
from app.web.templates import templates

router = APIRouter(prefix="/config")


@router.get("/trackers")
def trackers_page(request: Request, session: Session = Depends(get_session)):
    trackers = session.query(Tracker).all()
    return templates.TemplateResponse(request, "trackers.html", {"trackers": trackers})


@router.post("/trackers")
def create_tracker_page(
    label: str = Form(...),
    base_url: str = Form(...),
    api_token: str = Form(...),
    rate_limit_per_min: int = Form(30),
    session: Session = Depends(get_session),
):
    tracker = Tracker(
        label=label, adapter_type="unit3d", base_url=base_url,
        api_token=api_token, rate_limit_per_min=rate_limit_per_min,
    )
    session.add(tracker)
    session.commit()
    return RedirectResponse(url="/config/trackers", status_code=303)


@router.post("/trackers/{tracker_id}/toggle")
def toggle_tracker_page(tracker_id: int, session: Session = Depends(get_session)):
    tracker = session.get(Tracker, tracker_id)
    if tracker is not None:
        tracker.enabled = not tracker.enabled
        session.commit()
    return RedirectResponse(url="/config/trackers", status_code=303)


@router.post("/trackers/{tracker_id}/delete")
def delete_tracker_page(tracker_id: int, session: Session = Depends(get_session)):
    tracker = session.get(Tracker, tracker_id)
    if tracker is not None:
        session.delete(tracker)
        session.commit()
    return RedirectResponse(url="/config/trackers", status_code=303)
