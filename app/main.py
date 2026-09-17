from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import db, scheduler as scheduler_module
from app.api.disks import router as disks_router
from app.api.reviews import router as reviews_api_router
from app.api.runs import router as runs_router
from app.api.settings import router as settings_router
from app.config import load_settings
from app.web import router as web_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    engine = db.make_engine(settings.db_path)
    db.apply_schema(engine)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = db.make_session_factory(engine)
    app.state.scheduler = scheduler_module.create_scheduler(app.state.session_factory)
    yield
    app.state.scheduler.shutdown()


app = FastAPI(title="Ratio Guardian", lifespan=lifespan)
app.include_router(disks_router)
app.include_router(reviews_api_router)
app.include_router(runs_router)
app.include_router(settings_router)
app.include_router(web_router)
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")


@app.get("/health")
def health():
    return {"status": "ok"}
