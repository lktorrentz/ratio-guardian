"""API di configurazione per le MediaPath (docs/SPEC.md sezione 3)."""

import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.deps import get_session
from app.fs_scope import ScopeViolation, resolve_scoped
from app.models import Disk, MediaPath

router = APIRouter(tags=["media-paths"])


class MediaPathCreateRequest(BaseModel):
    relative_path: str
    content_type: str


class MediaPathUpdateRequest(BaseModel):
    enabled: bool | None = None


class MediaPathResponse(BaseModel):
    id: int
    disk_id: int
    relative_path: str
    content_type: str
    enabled: bool

    @classmethod
    def from_model(cls, mp: MediaPath) -> "MediaPathResponse":
        return cls(
            id=mp.id, disk_id=mp.disk_id, relative_path=mp.relative_path,
            content_type=mp.content_type, enabled=mp.enabled,
        )


def _get_disk_or_404(session: Session, disk_id: int) -> Disk:
    disk = session.get(Disk, disk_id)
    if disk is None:
        raise HTTPException(status_code=404, detail=f"Disco {disk_id} non trovato")
    return disk


def _get_media_path_or_404(session: Session, media_path_id: int) -> MediaPath:
    mp = session.get(MediaPath, media_path_id)
    if mp is None:
        raise HTTPException(status_code=404, detail=f"MediaPath {media_path_id} non trovata")
    return mp


class MediaPathValidationError(ValueError):
    pass


class MediaPathConflictError(ValueError):
    pass


def create_media_path_row(session: Session, disk: Disk, relative_path: str, content_type: str) -> MediaPath:
    if content_type not in ("movie", "tv"):
        raise MediaPathValidationError("content_type deve essere 'movie' o 'tv'")

    try:
        resolved = resolve_scoped(disk.root_path, relative_path)
    except ScopeViolation as exc:
        raise MediaPathValidationError(str(exc)) from exc
    if not os.path.isdir(resolved):
        raise MediaPathValidationError(f"Percorso non trovato: {relative_path!r}")

    media_path = MediaPath(disk_id=disk.id, relative_path=relative_path, content_type=content_type)
    session.add(media_path)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise MediaPathConflictError("Questa MediaPath esiste già per questo disco") from exc
    return media_path


def set_media_path_new_torrent_path(
    session: Session, media_path: MediaPath, new_torrent_rel_path: str | None
) -> MediaPath:
    """new_torrent_rel_path è relativo a disk.root_path, stessa convenzione
    di disk.torrents_rel_path — ma riguarda SOLO dove creare un NUOVO
    hardlink per questa libreria (e quale save_path comunicare al client),
    per i client che separano i completed per categoria (es.
    .../completed/movies). NON restringe la ricerca "già in seeding", che
    resta sempre su tutta disk.torrents_rel_path (un client può avere più
    sottocartelle che vanno comunque scansionate tutte per non generare
    doppioni). Deve essere quella stessa cartella o una sua sottocartella:
    mai un modo per spostare i seed di una libreria altrove sul disco
    (vedi CLAUDE.md, validazione "stesso disco")."""
    disk = media_path.disk
    if not new_torrent_rel_path:
        media_path.new_torrent_rel_path = None
        session.commit()
        return media_path

    if not disk.torrents_rel_path:
        raise MediaPathValidationError(f"Configura prima la cartella torrent del disco '{disk.label}'")

    try:
        resolved = resolve_scoped(disk.root_path, new_torrent_rel_path)
        disk_torrents_root = resolve_scoped(disk.root_path, disk.torrents_rel_path)
    except ScopeViolation as exc:
        raise MediaPathValidationError(str(exc)) from exc

    if resolved != disk_torrents_root and not resolved.startswith(disk_torrents_root + os.sep):
        raise MediaPathValidationError(
            f"Deve essere la cartella torrent del disco (/{disk.torrents_rel_path}) o una sua sottocartella"
        )
    if not os.path.isdir(resolved):
        raise MediaPathValidationError(f"Percorso non trovato: {new_torrent_rel_path!r}")

    media_path.new_torrent_rel_path = new_torrent_rel_path
    session.commit()
    return media_path


@router.get("/api/disks/{disk_id}/media-paths", response_model=list[MediaPathResponse])
def list_media_paths(disk_id: int, session: Session = Depends(get_session)):
    _get_disk_or_404(session, disk_id)
    rows = session.query(MediaPath).filter_by(disk_id=disk_id).all()
    return [MediaPathResponse.from_model(r) for r in rows]


@router.post("/api/disks/{disk_id}/media-paths", response_model=MediaPathResponse, status_code=201)
def create_media_path(disk_id: int, body: MediaPathCreateRequest, session: Session = Depends(get_session)):
    disk = _get_disk_or_404(session, disk_id)
    try:
        media_path = create_media_path_row(session, disk, body.relative_path, body.content_type)
    except MediaPathValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MediaPathConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return MediaPathResponse.from_model(media_path)


@router.patch("/api/media-paths/{media_path_id}", response_model=MediaPathResponse)
def update_media_path(media_path_id: int, body: MediaPathUpdateRequest, session: Session = Depends(get_session)):
    mp = _get_media_path_or_404(session, media_path_id)
    if body.enabled is not None:
        mp.enabled = body.enabled
    session.commit()
    return MediaPathResponse.from_model(mp)


@router.delete("/api/media-paths/{media_path_id}", status_code=204)
def delete_media_path(media_path_id: int, session: Session = Depends(get_session)):
    mp = _get_media_path_or_404(session, media_path_id)
    session.delete(mp)
    session.commit()
