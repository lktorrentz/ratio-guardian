"""Pagine HTML server-rendered (Jinja2 + Tailwind via CDN, form pieni —
vedi CLAUDE.md: niente build frontend pesante)."""

from fastapi import APIRouter

from app.web.dashboard import router as dashboard_router
from app.web.disks import router as disks_router
from app.web.reviews import router as reviews_router
from app.web.runs import router as runs_router
from app.web.settings import router as settings_router
from app.web.torrent_clients import router as torrent_clients_router
from app.web.trackers import router as trackers_router

router = APIRouter(tags=["web"])
for sub_router in (
    dashboard_router,
    disks_router,
    trackers_router,
    torrent_clients_router,
    settings_router,
    reviews_router,
    runs_router,
):
    router.include_router(sub_router)
