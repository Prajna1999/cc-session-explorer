"""Engine/session wiring.

``engine`` / ``SessionLocal`` are module-level singletons built from
``CC_CLOUD_DATABASE_URL``; tests build their own via ``create_engine_and_sessionmaker``
and override the ``get_db`` dependency.
"""

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import Base


def _normalize_url(url: str) -> str:
    return url.replace("postgres://", "postgresql://", 1)


def create_engine_and_sessionmaker(url: str) -> tuple[Engine, sessionmaker]:
    engine = create_engine(_normalize_url(url), pool_pre_ping=True)

    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _enable_fk(dbapi_conn, _record):  # pragma: no cover - dialect-specific
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return engine, maker


_settings = get_settings()
engine, SessionLocal = create_engine_and_sessionmaker(_settings.database_url)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables from metadata (dev convenience; prod uses Alembic)."""
    Base.metadata.create_all(engine)
