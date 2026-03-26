from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import DATABASE_URL


class Base(DeclarativeBase):
    pass


engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)

    if DATABASE_URL.startswith("sqlite"):
        _run_sqlite_migrations()


def _sqlite_has_column(conn: Session, table_name: str, column_name: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    return any(str(row[1]) == column_name for row in rows)


def _run_sqlite_migrations() -> None:
    with engine.begin() as conn:
        if not _sqlite_has_column(conn, "sources", "owner_username"):
            conn.execute(text("ALTER TABLE sources ADD COLUMN owner_username VARCHAR(64) NOT NULL DEFAULT 'admin'"))

        if not _sqlite_has_column(conn, "episodes", "owner_username"):
            conn.execute(text("ALTER TABLE episodes ADD COLUMN owner_username VARCHAR(64) NOT NULL DEFAULT 'admin'"))

        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sources_owner_username ON sources (owner_username)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_episodes_owner_username ON episodes (owner_username)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_settings_username ON user_settings (username)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_settings_key ON user_settings (key)"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
