"""API di configurazione per i client torrent (docs/SPEC.md sezione 10).

password non viene mai restituita in chiaro nelle risposte di lettura —
solo un flag has_password. Va inviata solo in creazione/aggiornamento.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_session
from app.models import TorrentClient

router = APIRouter(prefix="/api/torrent-clients", tags=["torrent-clients"])

SUPPORTED_ADAPTER_TYPES = ("qbittorrent",)


class TorrentClientCreateRequest(BaseModel):
    label: str
    adapter_type: str
    base_url: str
    username: str | None = None
    password: str | None = None
    enabled: bool = True


class TorrentClientUpdateRequest(BaseModel):
    label: str | None = None
    base_url: str | None = None
    username: str | None = None
    password: str | None = None
    enabled: bool | None = None


class TorrentClientResponse(BaseModel):
    id: int
    label: str
    adapter_type: str
    base_url: str
    username: str | None
    has_password: bool
    enabled: bool

    @classmethod
    def from_model(cls, tc: TorrentClient) -> "TorrentClientResponse":
        return cls(
            id=tc.id, label=tc.label, adapter_type=tc.adapter_type, base_url=tc.base_url,
            username=tc.username, has_password=bool(tc.password), enabled=tc.enabled,
        )


def _get_torrent_client_or_404(session: Session, torrent_client_id: int) -> TorrentClient:
    tc = session.get(TorrentClient, torrent_client_id)
    if tc is None:
        raise HTTPException(status_code=404, detail=f"Client torrent {torrent_client_id} non trovato")
    return tc


@router.get("", response_model=list[TorrentClientResponse])
def list_torrent_clients(session: Session = Depends(get_session)):
    return [TorrentClientResponse.from_model(t) for t in session.query(TorrentClient).all()]


@router.post("", response_model=TorrentClientResponse, status_code=201)
def create_torrent_client(body: TorrentClientCreateRequest, session: Session = Depends(get_session)):
    if body.adapter_type not in SUPPORTED_ADAPTER_TYPES:
        raise HTTPException(status_code=400, detail=f"adapter_type non supportato: {body.adapter_type!r}")
    tc = TorrentClient(
        label=body.label, adapter_type=body.adapter_type, base_url=body.base_url,
        username=body.username, password=body.password, enabled=body.enabled,
    )
    session.add(tc)
    session.commit()
    return TorrentClientResponse.from_model(tc)


@router.patch("/{torrent_client_id}", response_model=TorrentClientResponse)
def update_torrent_client(
    torrent_client_id: int, body: TorrentClientUpdateRequest, session: Session = Depends(get_session)
):
    tc = _get_torrent_client_or_404(session, torrent_client_id)
    if body.label is not None:
        tc.label = body.label
    if body.base_url is not None:
        tc.base_url = body.base_url
    if body.username is not None:
        tc.username = body.username
    if body.password:
        tc.password = body.password
    if body.enabled is not None:
        tc.enabled = body.enabled
    session.commit()
    return TorrentClientResponse.from_model(tc)


@router.delete("/{torrent_client_id}", status_code=204)
def delete_torrent_client(torrent_client_id: int, session: Session = Depends(get_session)):
    tc = _get_torrent_client_or_404(session, torrent_client_id)
    session.delete(tc)
    session.commit()
