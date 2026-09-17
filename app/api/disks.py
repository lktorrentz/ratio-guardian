"""File Browser API scoped-per-disco.

Vedi docs/SPEC.md sezione 5. Usata sia per selezionare MediaPath che per
creare/selezionare torrents_rel_path — un solo meccanismo di scoping
condiviso (app/fs_scope.py), mai duplicato.
"""

import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_session
from app.fs_scope import ScopeViolation, resolve_scoped
from app.models import Disk

router = APIRouter(prefix="/api/disks", tags=["disks"])


class BrowseEntry(BaseModel):
    name: str
    is_dir: bool


class BrowseResponse(BaseModel):
    disk_id: int
    current_path: str
    entries: list[BrowseEntry]


class MkdirRequest(BaseModel):
    path: str


class MkdirResponse(BaseModel):
    path: str
    created: bool


class VerifyResponse(BaseModel):
    consistent: bool
    warning: str | None = None


def _get_disk_or_404(session: Session, disk_id: int) -> Disk:
    disk = session.get(Disk, disk_id)
    if disk is None:
        raise HTTPException(status_code=404, detail=f"Disco {disk_id} non trovato")
    return disk


def _relative_to_root(root_path: str, candidate: str) -> str:
    rel = os.path.relpath(candidate, os.path.realpath(root_path))
    return "" if rel == "." else rel


def _resolve_or_400(disk: Disk, relative: str) -> str:
    try:
        return resolve_scoped(disk.root_path, relative)
    except ScopeViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{disk_id}/browse", response_model=BrowseResponse)
def browse(disk_id: int, path: str = "", session: Session = Depends(get_session)):
    disk = _get_disk_or_404(session, disk_id)
    candidate = _resolve_or_400(disk, path)

    if not os.path.isdir(candidate):
        raise HTTPException(status_code=404, detail=f"Percorso non trovato: {path!r}")

    entries = sorted(
        (BrowseEntry(name=e.name, is_dir=e.is_dir()) for e in os.scandir(candidate)),
        key=lambda e: e.name.lower(),
    )
    return BrowseResponse(
        disk_id=disk.id,
        current_path=_relative_to_root(disk.root_path, candidate),
        entries=entries,
    )


@router.post("/{disk_id}/mkdir", response_model=MkdirResponse, status_code=201)
def mkdir(disk_id: int, body: MkdirRequest, session: Session = Depends(get_session)):
    disk = _get_disk_or_404(session, disk_id)
    candidate = _resolve_or_400(disk, body.path)

    if os.path.exists(candidate):
        raise HTTPException(status_code=409, detail=f"La cartella esiste già: {body.path!r}")

    os.makedirs(candidate)
    return MkdirResponse(path=_relative_to_root(disk.root_path, candidate), created=True)


@router.post("/{disk_id}/verify", response_model=VerifyResponse)
def verify(disk_id: int, session: Session = Depends(get_session)):
    disk = _get_disk_or_404(session, disk_id)
    if not os.path.isdir(disk.root_path):
        raise HTTPException(status_code=404, detail=f"root_path non raggiungibile: {disk.root_path}")

    current_st_dev = os.stat(disk.root_path).st_dev

    if disk.st_dev is None:
        # Prima verifica: non c'è nulla con cui confrontare, stabiliamo la baseline.
        disk.st_dev = current_st_dev
        session.commit()
        return VerifyResponse(consistent=True)

    if current_st_dev != disk.st_dev:
        # Non sovrascriviamo mai silenziosamente: st_dev cambiato = possibile
        # rimonto/sostituzione del disco (vedi docs/SPEC.md sezione 3).
        return VerifyResponse(
            consistent=False,
            warning=(
                f"st_dev cambiato per il disco '{disk.label}' "
                f"({disk.st_dev} -> {current_st_dev}): possibile disco rimontato "
                "o sostituito. Verificare prima di procedere con hardlink."
            ),
        )

    return VerifyResponse(consistent=True)
