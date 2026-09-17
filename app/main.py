from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.api.disks import router as disks_router
from app.api.reviews import router as reviews_api_router
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
    yield


app = FastAPI(title="Ratio Guardian", lifespan=lifespan)
app.include_router(disks_router)
app.include_router(reviews_api_router)
app.include_router(web_router)


@app.get("/health")
def health():
    return {"status": "ok"}
