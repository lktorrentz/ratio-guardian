"""File Browser API scoped-per-disco.

Vedi docs/SPEC.md sezione 5. Usata sia per selezionare MediaPath che per
creare/selezionare torrents_rel_path — un solo meccanismo di scoping
condiviso (app/fs_scope.py), mai duplicato.
"""

import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
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


class DiskCreateRequest(BaseModel):
    label: str
    root_path: str


class DiskUpdateRequest(BaseModel):
    label: str | None = None
    torrents_rel_path: str | None = None


class DiskResponse(BaseModel):
    id: int
    label: str
    root_path: str
    torrents_rel_path: str | None
    st_dev: int | None

    @classmethod
    def from_model(cls, disk: Disk) -> "DiskResponse":
        return cls(
            id=disk.id, label=disk.label, root_path=disk.root_path,
            torrents_rel_path=disk.torrents_rel_path, st_dev=disk.st_dev,
        )


class DiskValidationError(ValueError):
    pass


class DiskConflictError(ValueError):
    pass


def _get_disk_or_404(session: Session, disk_id: int) -> Disk:
    disk = session.get(Disk, disk_id)
    if disk is None:
        raise HTTPException(status_code=404, detail=f"Disco {disk_id} non trovato")
    return disk


def is_within_scan_root(path: str, scan_root: str) -> bool:
    real = os.path.realpath(path)
    real_root = os.path.realpath(scan_root)
    return real == real_root or real.startswith(real_root + os.sep)


def list_available_mounts(scan_root: str, used_paths: set[str]) -> list[str]:
    """Sottocartelle di primo livello di scan_root non ancora assegnate a
    un Disk — sono i bind mount dei dischi fisici (vedi docker-compose.yml,
    che li monta 1:1 sotto scan_root, di default /mnt) non ancora aggiunti
    dalla Web UI. Nessuna dipendenza da config.yaml: basta il bind mount
    Docker perché un disco compaia qui."""
    if not os.path.isdir(scan_root):
        return []
    used_real = {os.path.realpath(p) for p in used_paths}
    mounts = [
        entry.path
        for entry in os.scandir(scan_root)
        if entry.is_dir() and os.path.realpath(entry.path) not in used_real
    ]
    return sorted(mounts)


def create_disk(session: Session, label: str, root_path: str, scan_root: str) -> Disk:
    if not os.path.isdir(root_path):
        raise DiskValidationError(f"root_path non è una cartella raggiungibile: {root_path}")
    if not is_within_scan_root(root_path, scan_root):
        raise DiskValidationError(
            f"root_path deve essere contenuto in disk_scan_root ({scan_root})"
        )
    disk = Disk(label=label, root_path=root_path)
    session.add(disk)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise DiskConflictError(f"Esiste già un disco con root_path {root_path!r}") from exc
    return disk


def verify_disk(session: Session, disk: Disk) -> VerifyResponse:
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
    return verify_disk(session, disk)


@router.get("", response_model=list[DiskResponse])
def list_disks(session: Session = Depends(get_session)):
    return [DiskResponse.from_model(d) for d in session.query(Disk).all()]


@router.post("", response_model=DiskResponse, status_code=201)
def create_disk_endpoint(body: DiskCreateRequest, request: Request, session: Session = Depends(get_session)):
    try:
        disk = create_disk(session, body.label, body.root_path, request.app.state.settings.disk_scan_root)
    except DiskValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DiskConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return DiskResponse.from_model(disk)


@router.patch("/{disk_id}", response_model=DiskResponse)
def update_disk(disk_id: int, body: DiskUpdateRequest, session: Session = Depends(get_session)):
    disk = _get_disk_or_404(session, disk_id)
    if body.label is not None:
        disk.label = body.label
    if body.torrents_rel_path is not None:
        disk.torrents_rel_path = body.torrents_rel_path or None
    session.commit()
    return DiskResponse.from_model(disk)


@router.delete("/{disk_id}", status_code=204)
def delete_disk(disk_id: int, session: Session = Depends(get_session)):
    disk = _get_disk_or_404(session, disk_id)
    session.delete(disk)
    session.commit()
