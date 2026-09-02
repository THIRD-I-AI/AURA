"""
semantic_builder tests.

Tier A (pure Python, no optional deps).

`semantic_builder` backs `POST /semantic/models/from-file/{file_id}`
(aurabackend/api_gateway/routers/pipelines.py::auto_generate_model_from_file).
Before this module existed the import failed, `semantic_builder` was set to
`None`, and the route always returned `{"status": "error", ...}` with HTTP 200
regardless of caller/input — a silently-broken endpoint. These tests cover the
real generator against every profile shape the repo actually persists via
`metadata_store.repository.upsert_dataset_profile`, plus an end-to-end check
that a generated model round-trips through the repository the way the route
uses it.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from semantic_builder import SemanticModelBuilder, semantic_builder

# ── Column-profile shape (shared.data_profile.profile_columns) ─────────────

def test_generate_model_from_column_profile_shape():
    profile = {
        "product_id": {"dtype": "id", "distinct": 50, "null_ratio": 0.0, "sample": ["1", "2"]},
        "product_name": {"dtype": "categorical", "distinct": 50, "null_ratio": 0.0, "sample": ["Widget A"]},
        "revenue": {
            "dtype": "numeric", "distinct": 900, "null_ratio": 0.05, "sample": ["100.0"],
            "stats": {"count": 950, "mean": 500.0, "min": 10.0, "max": 10000.0},
        },
    }

    model = semantic_builder.generate_model_from_profile(
        file_id="file-123", dataset_name="sales", profile=profile,
    )

    assert model["name"] == "sales"
    assert model["source"] == {"file_id": "file-123"}
    assert "auto-generated" in model["tags"]
    assert len(model["fields"]) == 3

    by_name = {f["name"]: f for f in model["fields"]}
    assert by_name["product_id"]["field_type"] == "dimension"
    assert by_name["product_name"]["field_type"] == "dimension"
    assert by_name["revenue"]["field_type"] == "measure"
    assert by_name["revenue"]["aggregation"] == "sum"
    assert by_name["revenue"]["data_type"] == "numeric"
    # Stats/sample beyond dtype flow into field metadata rather than being dropped.
    assert by_name["revenue"]["metadata"]["stats"]["mean"] == 500.0


# ── Connector-ingestion shape (api_gateway/routers/connections.py) ─────────

def test_generate_model_from_connector_schema_shape():
    profile = {
        "source": "postgres",
        "table": "orders",
        "schema": {"order_id": "INTEGER", "customer_name": "VARCHAR", "total": "NUMERIC"},
        "batches": 3,
        "drift_events": 0,
    }

    model = semantic_builder.generate_model_from_profile(
        file_id="conn-1", dataset_name="postgres:orders", profile=profile,
    )

    by_name = {f["name"]: f for f in model["fields"]}
    assert len(by_name) == 3
    assert by_name["order_id"]["field_type"] == "measure"  # INTEGER -> numeric hint
    assert by_name["order_id"]["aggregation"] == "sum"
    assert by_name["customer_name"]["field_type"] == "dimension"
    assert by_name["total"]["field_type"] == "measure"


# ── Legacy columns_profile shape ────────────────────────────────────────────

def test_generate_model_from_columns_profile_key_shape():
    profile = {
        "columns_profile": {
            "product_id": {"data_type": "integer", "non_null": 1000, "distinct": 50},
            "revenue": {"data_type": "numeric", "non_null": 950, "distinct": 900},
        }
    }

    model = semantic_builder.generate_model_from_profile(
        file_id="file-2", dataset_name="sales_v2", profile=profile,
    )

    by_name = {f["name"]: f for f in model["fields"]}
    assert by_name["product_id"]["field_type"] == "measure"  # 'integer' matches numeric hint
    assert by_name["revenue"]["field_type"] == "measure"
    assert by_name["revenue"]["aggregation"] == "sum"


# ── Degenerate input: unknown/empty profile shape ───────────────────────────

def test_generate_model_from_unrecognized_profile_shape_yields_no_fields():
    model = semantic_builder.generate_model_from_profile(
        file_id="file-3", dataset_name="mystery", profile={"note": "no column info here"},
    )
    assert model["fields"] == []
    assert model["name"] == "mystery"


def test_generate_model_handles_empty_profile():
    model = semantic_builder.generate_model_from_profile(file_id="file-4", dataset_name="empty", profile={})
    assert model["fields"] == []


def test_semantic_model_builder_is_a_class_with_singleton_instance():
    assert isinstance(semantic_builder, SemanticModelBuilder)
    builder = SemanticModelBuilder()
    result = builder.generate_model_from_profile(file_id="x", dataset_name="y", profile={})
    assert result["name"] == "y"


# ── End-to-end: generated payload round-trips through the repository ──────
# the same repository call the route makes (upsert_semantic_model with the
# dict this module returns), without driving the ASGI lifespan (backend.md).

@pytest.fixture
async def db_session():
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from metadata_store import models  # noqa: F401 — registers tables on Base.metadata
    from metadata_store.db import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_generated_model_persists_via_repository(db_session):
    from metadata_store.repository import MetadataRepository

    repo = MetadataRepository(db_session)
    await repo.upsert_dataset_profile(
        file_id="file-5",
        dataset_name="orders",
        profile={"order_id": {"dtype": "id"}, "total": {"dtype": "numeric", "stats": {"mean": 42.0}}},
    )

    profile_record = await repo.get_dataset_profile("file-5")
    assert profile_record is not None

    model_payload = semantic_builder.generate_model_from_profile(
        file_id="file-5",
        dataset_name=profile_record.dataset_name or "dataset_file-5",
        profile=profile_record.profile,
    )

    model = await repo.upsert_semantic_model(
        model_id=None,
        name=model_payload["name"],
        description=model_payload["description"],
        source=model_payload["source"],
        tags=model_payload["tags"],
        fields=model_payload["fields"],
        workspace_id="tenant-a",
    )

    assert model.name == "orders"
    assert len(model.fields) == 2
    field_by_name = {f.name: f for f in model.fields}
    assert field_by_name["total"].field_type == "measure"
    assert field_by_name["total"].aggregation == "sum"
