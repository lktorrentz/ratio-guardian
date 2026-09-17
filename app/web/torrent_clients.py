"""Pagina web di configurazione client torrent (docs/SPEC.md sezione 10).

Un solo adapter_type supportato oggi (qbittorrent), impostato server-side.
password non viene mai renderizzata in chiaro nel template.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.deps import get_session
from app.models import TorrentClient
from app.web.templates import templates

router = APIRouter(prefix="/config")


@router.get("/torrent-clients")
def torrent_clients_page(request: Request, session: Session = Depends(get_session)):
    torrent_clients = session.query(TorrentClient).all()
    return templates.TemplateResponse(request, "torrent_clients.html", {"torrent_clients": torrent_clients})


@router.post("/torrent-clients")
def create_torrent_client_page(
    label: str = Form(...),
    base_url: str = Form(...),
    username: str = Form(""),
    password: str = Form(""),
    session: Session = Depends(get_session),
):
    tc = TorrentClient(
        label=label, adapter_type="qbittorrent", base_url=base_url,
        username=username or None, password=password or None,
    )
    session.add(tc)
    session.commit()
    return RedirectResponse(url="/config/torrent-clients", status_code=303)


@router.post("/torrent-clients/{torrent_client_id}/toggle")
def toggle_torrent_client_page(torrent_client_id: int, session: Session = Depends(get_session)):
    tc = session.get(TorrentClient, torrent_client_id)
    if tc is not None:
        tc.enabled = not tc.enabled
        session.commit()
    return RedirectResponse(url="/config/torrent-clients", status_code=303)


@router.post("/torrent-clients/{torrent_client_id}/delete")
def delete_torrent_client_page(torrent_client_id: int, session: Session = Depends(get_session)):
    tc = session.get(TorrentClient, torrent_client_id)
    if tc is not None:
        session.delete(tc)
        session.commit()
    return RedirectResponse(url="/config/torrent-clients", status_code=303)
