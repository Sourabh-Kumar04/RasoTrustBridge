import logging
from typing import Generator
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from app.config import get_settings

logger = logging.getLogger("trustbridge.database")
settings = get_settings()

db_url = settings.DATABASE_URL
is_sqlite = db_url.startswith("sqlite")


def _create_db_engine(url: str):
    _sqlite = url.startswith("sqlite")
    _args = {"check_same_thread": False} if _sqlite else {}
    _eng = create_engine(
        url,
        connect_args=_args,
        pool_pre_ping=True,
    )
    if _sqlite:
        @event.listens_for(_eng, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()
    return _eng


engine = _create_db_engine(db_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    global engine, SessionLocal
    # Import all models so that Base.metadata has all table definitions
    import app.models  # noqa: F401

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        if not db_url.startswith("sqlite") and settings.ENVIRONMENT != "production":
            logger.warning(
                "PostgreSQL connection to %s failed (%s). Falling back to SQLite dev fallback.",
                db_url,
                e,
            )
            fallback_url = "sqlite:///./trustbridge.db"
            engine = _create_db_engine(fallback_url)
            SessionLocal.configure(bind=engine)
        else:
            raise

    Base.metadata.create_all(bind=engine)
