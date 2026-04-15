"""
Alembic migration runner
=========================
Small async wrapper so the API gateway lifespan can ``await`` an
``alembic upgrade head`` without shelling out. Alembic itself is sync,
so the actual work runs in a thread via ``asyncio.to_thread``.

The Alembic config lives at ``aurabackend/alembic.ini`` relative to
this file; env.py pulls the DB URL from ``metadata_store.db.DATABASE_URL``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _BACKEND_ROOT / "alembic.ini"


def _upgrade_head_sync() -> None:
    cfg = Config(str(_ALEMBIC_INI))
    # Ensure script_location resolves relative to aurabackend/ regardless
    # of the process's current working directory.
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    command.upgrade(cfg, "head")


async def run_migrations_to_head() -> None:
    """Run ``alembic upgrade head`` in a worker thread."""
    await asyncio.to_thread(_upgrade_head_sync)
