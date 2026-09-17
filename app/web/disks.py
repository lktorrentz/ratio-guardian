"""Pagine web di configurazione dischi e media path (docs/SPEC.md sez. 3)."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.disks import DiskConflictError, DiskValidationError, create_disk, verify_disk
from app.api.media_paths import MediaPathConflictError, MediaPathValidationError, create_media_path_row
from app.deps import get_session
from app.models import Disk, MediaPath
from app.web.templates import templates

router = APIRouter(prefix="/config")


@router.get("/disks")
def disks_page(request: Request, session: Session = Depends(get_session)):
    disks = session.query(Disk).all()
    used_mounts = {d.root_path for d in disks}
    available_mounts = [m for m in request.app.state.settings.disks if m not in used_mounts]
    return templates.TemplateResponse(
        request,
        "disks.html",
        {"disks": disks, "available_mounts": available_mounts, "error": request.query_params.get("error")},
    )


@router.post("/disks")
def create_disk_page(
    request: Request,
    label: str = Form(...),
    root_path: str = Form(...),
    session: Session = Depends(get_session),
):
    try:
        create_disk(session, label, root_path, request.app.state.settings.disks)
    except (DiskValidationError, DiskConflictError) as exc:
        return RedirectResponse(url=f"/config/disks?error={quote(str(exc))}", status_code=303)
    return RedirectResponse(url="/config/disks", status_code=303)


@router.post("/disks/{disk_id}/delete")
def delete_disk_page(disk_id: int, session: Session = Depends(get_session)):
    disk = session.get(Disk, disk_id)
    if disk is not None:
        session.delete(disk)
        session.commit()
    return RedirectResponse(url="/config/disks", status_code=303)


@router.post("/disks/{disk_id}/verify")
def verify_disk_page(disk_id: int, session: Session = Depends(get_session)):
    disk = session.get(Disk, disk_id)
    url = f"/config/disks/{disk_id}"
    if disk is not None:
        result = verify_disk(session, disk)
        if result.warning:
            url += f"?warning={quote(result.warning)}"
    return RedirectResponse(url=url, status_code=303)


@router.get("/disks/{disk_id}")
def disk_detail_page(disk_id: int, request: Request, session: Session = Depends(get_session)):
    disk = session.get(Disk, disk_id)
    if disk is None:
        return RedirectResponse(url="/config/disks", status_code=303)
    media_paths = session.query(MediaPath).filter_by(disk_id=disk_id).all()
    return templates.TemplateResponse(
        request,
        "disk_detail.html",
        {
            "disk": disk,
            "media_paths": media_paths,
            "warning": request.query_params.get("warning"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/disks/{disk_id}/torrents-path")
def set_torrents_path_page(disk_id: int, torrents_rel_path: str = Form(...), session: Session = Depends(get_session)):
    disk = session.get(Disk, disk_id)
    if disk is not None:
        disk.torrents_rel_path = torrents_rel_path or None
        session.commit()
    return RedirectResponse(url=f"/config/disks/{disk_id}", status_code=303)


@router.post("/disks/{disk_id}/media-paths")
def create_media_path_page(
    disk_id: int,
    relative_path: str = Form(...),
    content_type: str = Form(...),
    session: Session = Depends(get_session),
):
    disk = session.get(Disk, disk_id)
    url = f"/config/disks/{disk_id}"
    if disk is not None:
        try:
            create_media_path_row(session, disk, relative_path, content_type)
        except (MediaPathValidationError, MediaPathConflictError) as exc:
            url += f"?error={quote(str(exc))}"
    return RedirectResponse(url=url, status_code=303)


@router.post("/media-paths/{media_path_id}/delete")
def delete_media_path_page(media_path_id: int, session: Session = Depends(get_session)):
    mp = session.get(MediaPath, media_path_id)
    disk_id = mp.disk_id if mp is not None else None
    if mp is not None:
        session.delete(mp)
        session.commit()
    return RedirectResponse(url=f"/config/disks/{disk_id}" if disk_id else "/config/disks", status_code=303)


@router.post("/media-paths/{media_path_id}/toggle")
def toggle_media_path_page(media_path_id: int, session: Session = Depends(get_session)):
    mp = session.get(MediaPath, media_path_id)
    disk_id = mp.disk_id if mp is not None else None
    if mp is not None:
        mp.enabled = not mp.enabled
        session.commit()
    return RedirectResponse(url=f"/config/disks/{disk_id}" if disk_id else "/config/disks", status_code=303)
