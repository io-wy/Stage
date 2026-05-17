from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from openagents_orchestration.app.core.config import get_settings


class Base(DeclarativeBase):
    """Base class for all ORM models."""


settings = get_settings()

connect_args = {}
if settings.DATABASE_URL.startswith("sqlite") and not settings.SQLITE_CONNECT_ARGS_CHECK_SAME_THREAD:
    connect_args = {"check_same_thread": False}

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(bind=engine, class_=Session, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that provides a SQLAlchemy session."""

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
