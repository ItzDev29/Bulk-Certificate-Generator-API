import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.api.routes import router
from app.config import Settings, get_settings
from app.database import Base, create_db_engine, create_session_factory
from app.services.processor import JobDispatcher
from app.services.storage import CertificateStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _ensure_sqlite_dir(url: str) -> None:
    prefix = "sqlite:///"
    if url.startswith(prefix) and ":memory:" not in url:
        Path(url[len(prefix):]).parent.mkdir(parents=True, exist_ok=True)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory: every dependency is built from `settings`, which keeps
    tests isolated (own DB file, own storage dir) and avoids import-time side effects."""
    settings = settings or get_settings()
    engine = create_db_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    storage = CertificateStorage(settings.storage_dir)
    dispatcher = JobDispatcher(
        session_factory,
        storage,
        workers=settings.worker_threads,
        inline=settings.processing_mode == "inline",
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        _ensure_sqlite_dir(settings.database_url)
        storage.ensure_root()
        Base.metadata.create_all(engine)  # use Alembic migrations for real deployments
        if settings.recover_jobs_on_startup:
            recovered = dispatcher.recover()
            if recovered:
                logger.info("Re-queued %d unfinished job(s)", recovered)
        yield
        dispatcher.shutdown()
        engine.dispose()

    app = FastAPI(
        title="Bulk Certificate Generator",
        version="1.0.0",
        description="Submit a list of recipients, get a certificate PDF for each one.",
        lifespan=lifespan,
    )
    app.state.session_factory = session_factory
    app.state.storage = storage
    app.state.dispatcher = dispatcher
    app.include_router(router)
    return app


app = create_app()
