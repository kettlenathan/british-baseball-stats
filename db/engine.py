"""Engine/session factory for the SQLite database."""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from config import DB_URL
from db.models import Base

engine = create_engine(DB_URL)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Session:
    return SessionLocal()


def create_all() -> None:
    """Create all tables directly from models (used by tests/dev; production
    schema changes should go through Alembic migrations)."""
    Base.metadata.create_all(engine)
