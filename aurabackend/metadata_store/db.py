from __future__ import annotations

import os
from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


def _default_sqlite_path() -> str:
    base_dir = Path(os.getenv("AURA_DATA_DIR", Path(__file__).resolve().parent / ".." / "data"))
    base_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{(base_dir / 'metadata.db').resolve()}"


DATABASE_URL = os.getenv("METADATA_DATABASE_URL", _default_sqlite_path())
ECHO_SQL = os.getenv("METADATA_SQL_ECHO", "false").lower() == "true"


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(DATABASE_URL, echo=ECHO_SQL, future=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
