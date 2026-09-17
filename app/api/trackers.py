"""API di configurazione per i tracker (docs/SPEC.md sezione 7).

api_token non viene mai restituito in chiaro nelle risposte di lettura —
solo un flag has_api_token. Va inviato solo in creazione/aggiornamento.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_session
from app.models import Tracker

router = APIRouter(prefix="/api/trackers", tags=["trackers"])

SUPPORTED_ADAPTER_TYPES = ("unit3d",)


class TrackerCreateRequest(BaseModel):
    label: str
    adapter_type: str
    base_url: str
    api_token: str
    rate_limit_per_min: int = 30
    enabled: bool = True


class TrackerUpdateRequest(BaseModel):
    label: str | None = None
    base_url: str | None = None
    api_token: str | None = None
    rate_limit_per_min: int | None = None
    enabled: bool | None = None


class TrackerResponse(BaseModel):
    id: int
    label: str
    adapter_type: str
    base_url: str
    has_api_token: bool
    rate_limit_per_min: int | None
    enabled: bool

    @classmethod
    def from_model(cls, tracker: Tracker) -> "TrackerResponse":
        return cls(
            id=tracker.id, label=tracker.label, adapter_type=tracker.adapter_type,
            base_url=tracker.base_url, has_api_token=bool(tracker.api_token),
            rate_limit_per_min=tracker.rate_limit_per_min, enabled=tracker.enabled,
        )


def _get_tracker_or_404(session: Session, tracker_id: int) -> Tracker:
    tracker = session.get(Tracker, tracker_id)
    if tracker is None:
        raise HTTPException(status_code=404, detail=f"Tracker {tracker_id} non trovato")
    return tracker


@router.get("", response_model=list[TrackerResponse])
def list_trackers(session: Session = Depends(get_session)):
    return [TrackerResponse.from_model(t) for t in session.query(Tracker).all()]


@router.post("", response_model=TrackerResponse, status_code=201)
def create_tracker(body: TrackerCreateRequest, session: Session = Depends(get_session)):
    if body.adapter_type not in SUPPORTED_ADAPTER_TYPES:
        raise HTTPException(status_code=400, detail=f"adapter_type non supportato: {body.adapter_type!r}")
    tracker = Tracker(
        label=body.label, adapter_type=body.adapter_type, base_url=body.base_url,
        api_token=body.api_token, rate_limit_per_min=body.rate_limit_per_min, enabled=body.enabled,
    )
    session.add(tracker)
    session.commit()
    return TrackerResponse.from_model(tracker)


@router.patch("/{tracker_id}", response_model=TrackerResponse)
def update_tracker(tracker_id: int, body: TrackerUpdateRequest, session: Session = Depends(get_session)):
    tracker = _get_tracker_or_404(session, tracker_id)
    if body.label is not None:
        tracker.label = body.label
    if body.base_url is not None:
        tracker.base_url = body.base_url
    if body.api_token:
        tracker.api_token = body.api_token
    if body.rate_limit_per_min is not None:
        tracker.rate_limit_per_min = body.rate_limit_per_min
    if body.enabled is not None:
        tracker.enabled = body.enabled
    session.commit()
    return TrackerResponse.from_model(tracker)


@router.delete("/{tracker_id}", status_code=204)
def delete_tracker(tracker_id: int, session: Session = Depends(get_session)):
    tracker = _get_tracker_or_404(session, tracker_id)
    session.delete(tracker)
    session.commit()
