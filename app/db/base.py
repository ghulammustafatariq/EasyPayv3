"""
app/db/base.py — EasyPay v3.0 Async Database Engine

Uses lazy initialisation so the engine is only created during the
application lifespan (main.py), not at import time. This prevents
startup failures when DATABASE_URL is missing during local dev.

Critical Point 1 — SQLAlchemy 2.0 async-only: always await db.execute().
Critical Point 2 — URL prefix fix applied in config.async_database_url.
"""
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all SQLAlchemy models."""
    pass


# Module-level references — populated by init_engine() during lifespan startup.
engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def init_engine(database_url: str, echo: bool = False) -> None:
    """
    Create the async engine and session factory.

    Called once from the FastAPI lifespan. Subsequent calls are no-ops
    if the engine is already initialised.
    """
    global engine, _session_factory
    if engine is not None:
        return  # already initialised
    engine = create_async_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )
    _session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency — yields an async DB session per request.

    Returns 503 if init_engine() has not been called yet (no DATABASE_URL).
    """
    if _session_factory is None:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=503,
            detail="Database not available. Please check server configuration.",
        )
    async with _session_factory() as session:
        yield session
