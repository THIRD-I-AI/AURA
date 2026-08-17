"""
Pipeline Execution Tests
========================
Hermetic, no-network tests that a pipeline run actually reads data.

Two live bugs made every pipeline run return status "failed" /
rows_read 0 with HTTP 200:

1. FILE sources: the engine resolved uploads against a hardcoded,
   import-time constant that ignored AURA_UPLOADS_ROOT and the
   per-tenant upload subfolder the rest of the app writes to
   (api_gateway/routers/workspaces.py::tenant_upload_dir). The engine
   must be told the caller's resolved upload dir per-request.
2. DUCKDB sources: the engine only ever opened an empty ":memory:"
   connection and never ATTACHed the external .duckdb file named in
   source.connection["database"], so any table reference raised
   "Catalog Error: Table with name X does not exist!" even though the
   table exists on disk.

Both tests below exercise PipelineEngine.execute(..., upload_dir=...) —
the fixed public contract the router (pipelines.py) calls through — and
must fail against the pre-fix engine.py (which has no upload_dir
parameter at all).
"""
from __future__ import annotations

import duckdb
import pytest

from pipeline.engine import PipelineEngine
from pipeline.models import (
    Pipeline,
    PipelineSink,
    PipelineSource,
    PipelineStatus,
    SinkType,
    SourceType,
)


@pytest.mark.asyncio
async def test_file_source_reads_real_csv_through_tenant_upload_dir(tmp_path):
    """A FILE source resolves through a tenant-scoped upload dir (not a
    flat/hardcoded one) and reports the real row count + values."""
    tenant_dir = tmp_path / "uploads" / "tenant_abc123"
    tenant_dir.mkdir(parents=True)
    csv_path = tenant_dir / "customers.csv"
    csv_path.write_text("id,name,revenue\n1,Alice,100\n2,Bob,250\n3,Carol,75\n", encoding="utf-8")

    pipeline = Pipeline(
        name="file-source-test",
        source=PipelineSource(type=SourceType.FILE, file_name="customers.csv"),
        sink=PipelineSink(type=SinkType.PREVIEW),
    )

    engine = PipelineEngine()
    run = await engine.execute(pipeline, preview_only=True, upload_dir=str(tenant_dir))

    assert run.status == PipelineStatus.SUCCESS, run.error
    assert run.rows_read == 3
    assert run.columns_in == ["id", "name", "revenue"]
    names = {row["name"] for row in run.preview_data}
    assert names == {"Alice", "Bob", "Carol"}


@pytest.mark.asyncio
async def test_duckdb_source_reads_real_external_db_table(tmp_path):
    """A DUCKDB source with connection.database names an external .duckdb
    file; the engine must ATTACH it (not just query an empty :memory:)."""
    tenant_dir = tmp_path / "uploads" / "tenant_abc123"
    tenant_dir.mkdir(parents=True)
    db_path = tenant_dir / "warehouse.duckdb"

    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE regions (id INTEGER, name VARCHAR, population BIGINT)")
    con.executemany(
        "INSERT INTO regions VALUES (?, ?, ?)",
        [(1, "North", 1000), (2, "South", 2000), (3, "East", 1500)],
    )
    con.close()

    pipeline = Pipeline(
        name="duckdb-source-test",
        source=PipelineSource(
            type=SourceType.DUCKDB,
            table="regions",
            connection={"database": "warehouse.duckdb"},
        ),
        sink=PipelineSink(type=SinkType.PREVIEW),
    )

    engine = PipelineEngine()
    run = await engine.execute(pipeline, preview_only=True, upload_dir=str(tenant_dir))

    assert run.status == PipelineStatus.SUCCESS, run.error
    assert run.rows_read == 3
    populations = {row["population"] for row in run.preview_data}
    assert populations == {1000, 2000, 1500}
