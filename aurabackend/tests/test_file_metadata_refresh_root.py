"""BUG-382: the file-metadata lifespan worker walked <app>/data/uploads -- empty in the
container -- instead of AURA_UPLOADS_ROOT, so each tick pruned every tenant's cached rows."""
from __future__ import annotations

import asyncio


def test_the_refresh_worker_scans_the_configured_uploads_root(monkeypatch, tmp_path):
    from api_gateway import main, persistence
    from api_gateway.routers import workspaces

    monkeypatch.setattr(workspaces, "_UPLOADS_ROOT", str(tmp_path / "uploads"))
    monkeypatch.setattr("shared.upload_migration.migrate_flat_uploads_to_default", lambda root: None)
    scanned = []

    async def _scenario():
        stop = asyncio.Event()

        async def _refresh(upload_dir):
            scanned.append(upload_dir)
            stop.set()
            return {"indexed": 0, "skipped": 0, "pruned": 0}

        monkeypatch.setattr(persistence, "refresh_stale_file_metadata", _refresh)
        await asyncio.wait_for(main._file_metadata_refresh_loop(stop), timeout=5)

    asyncio.run(_scenario())
    assert scanned == [str(tmp_path / "uploads")]
