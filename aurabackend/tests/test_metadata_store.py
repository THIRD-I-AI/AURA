"""
Sprint S31a — Metadata store tests.

Tier A (pure Python + numpy, no optional deps).

Covers:
  * ORM model instantiation (User, DataSource, Document, DatasetProfile, etc.)
  * _serialize_user helper
  * _normalize_vector utility
  * MetadataRepository CRUD against in-memory SQLite
    - upsert_user / get_user
    - upsert_dataset_profile / get_dataset_profile
    - upsert_document with embedding / list_embeddings
    - upsert_semantic_model with fields / list / get
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metadata_store.db import Base
from metadata_store.models import (
    DARInsight,
    DatasetProfile,
    DataSource,
    Document,
    DocumentEmbedding,
    SchemaColumn,
    SemanticField,
    SemanticModel,
    User,
)

# ── ORM instantiation tests ───────────────────────────────────────

class TestUser:
    def test_explicit_fields(self):
        u = User(id="u1", name="Alice", email="alice@example.com")
        assert u.id == "u1"
        assert u.name == "Alice"
        assert u.email == "alice@example.com"

    def test_optional_email(self):
        u = User(id="u2", name="Bob")
        assert u.email is None

    def test_password_hash_optional(self):
        u = User(id="u3", name="Carol")
        assert u.password_hash is None


class TestDataSourceModel:
    def test_explicit_fields(self):
        ds = DataSource(id="ds1", name="sales_db", type="postgresql")
        assert ds.name == "sales_db"
        assert ds.type == "postgresql"
        assert ds.connection_id is None


class TestDocument:
    def test_explicit_fields(self):
        d = Document(id="d1", title="Schema: sales", body="columns: id, amount")
        assert d.id == "d1"
        assert d.title == "Schema: sales"
        assert d.body == "columns: id, amount"


class TestDocumentEmbedding:
    def test_explicit_fields(self):
        de = DocumentEmbedding(document_id="d1", vector=[0.1, 0.2, 0.3])
        assert de.document_id == "d1"
        assert de.vector == [0.1, 0.2, 0.3]


class TestSchemaColumn:
    def test_explicit_fields(self):
        sc = SchemaColumn(
            source_id="upload_sales",
            table_name="sales",
            column_name="Revenue",
            column_name_lower="revenue",
            data_type="DOUBLE",
        )
        assert sc.source_id == "upload_sales"
        assert sc.column_name == "Revenue"
        assert sc.column_name_lower == "revenue"
        assert sc.ordinal_position is None
        assert sc.description is None


class TestDARInsight:
    def test_explicit_fields(self):
        di = DARInsight(
            id="dar1",
            source_id="src1",
            table_name="orders",
            question="What is the trend?",
            summary="Revenue is increasing",
        )
        assert di.id == "dar1"
        assert di.source_id == "src1"
        assert di.question == "What is the trend?"
        assert di.run_id is None
        assert di.error is None


class TestDatasetProfile:
    def test_explicit_fields(self):
        dp = DatasetProfile(id="dp1", file_id="f1")
        assert dp.id == "dp1"
        assert dp.file_id == "f1"
        assert dp.dataset_name is None
        assert dp.rows_count is None
        assert dp.columns_count is None


class TestSemanticModel:
    def test_explicit_fields(self):
        sm = SemanticModel(id="sm1", name="Revenue Model")
        assert sm.id == "sm1"
        assert sm.name == "Revenue Model"
        assert sm.description is None


class TestSemanticField:
    def test_explicit_fields(self):
        sf = SemanticField(
            id="sf1",
            model_id="sm1",
            name="total_revenue",
            field_type="measure",
            data_type="DOUBLE",
            expression="SUM(revenue)",
            aggregation="sum",
        )
        assert sf.name == "total_revenue"
        assert sf.field_type == "measure"
        assert sf.expression == "SUM(revenue)"
        assert sf.description is None


# ── _serialize_user helper ─────────────────────────────────────────

class TestSerializeUser:
    def test_basic(self):
        from datetime import datetime, timezone

        from metadata_store.main import _serialize_user

        u = User(id="u1", name="Alice", email="alice@test.com")
        u.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        u.updated_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
        result = _serialize_user(u)
        assert result["id"] == "u1"
        assert result["name"] == "Alice"
        assert result["email"] == "alice@test.com"
        assert result["created_at"] == "2026-01-01T00:00:00+00:00"
        assert result["updated_at"] == "2026-01-02T00:00:00+00:00"

    def test_none_dates(self):
        from metadata_store.main import _serialize_user

        u = User(id="u2", name="Bob")
        u.created_at = None
        u.updated_at = None
        result = _serialize_user(u)
        assert result["created_at"] is None
        assert result["updated_at"] is None


# ── _normalize_vector utility ──────────────────────────────────────

class TestNormalizeVector:
    def test_unit_vector(self):
        from metadata_store.repository import _normalize_vector
        result = _normalize_vector([3.0, 4.0])
        assert len(result) == 2
        assert abs(result[0] - 0.6) < 1e-5
        assert abs(result[1] - 0.8) < 1e-5

    def test_normalized_has_unit_length(self):
        from metadata_store.repository import _normalize_vector
        result = _normalize_vector([1.0, 2.0, 3.0, 4.0])
        norm = sum(x * x for x in result) ** 0.5
        assert abs(norm - 1.0) < 1e-5

    def test_zero_vector_returns_zeros(self):
        from metadata_store.repository import _normalize_vector
        result = _normalize_vector([0.0, 0.0, 0.0])
        assert result == [0.0, 0.0, 0.0]

    def test_single_element(self):
        from metadata_store.repository import _normalize_vector
        result = _normalize_vector([5.0])
        assert abs(result[0] - 1.0) < 1e-5


# ── MetadataRepository CRUD (async, in-memory SQLite) ──────────────

@pytest.fixture
async def db_session():
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
class TestRepositoryUsers:
    async def test_upsert_creates_new(self, db_session):
        from metadata_store.repository import MetadataRepository
        repo = MetadataRepository(db_session)
        user = await repo.upsert_user("alice", name="Alice", email="alice@test.com")
        assert user.id == "alice"
        assert user.name == "Alice"
        assert user.email == "alice@test.com"

    async def test_upsert_updates_existing(self, db_session):
        from metadata_store.repository import MetadataRepository
        repo = MetadataRepository(db_session)
        await repo.upsert_user("alice", name="Alice", email="old@test.com")
        updated = await repo.upsert_user("alice", name="Alice Smith", email="new@test.com")
        assert updated.name == "Alice Smith"
        assert updated.email == "new@test.com"

    async def test_get_user_found(self, db_session):
        from metadata_store.repository import MetadataRepository
        repo = MetadataRepository(db_session)
        await repo.upsert_user("bob", name="Bob")
        user = await repo.get_user("bob")
        assert user is not None
        assert user.name == "Bob"

    async def test_get_user_not_found(self, db_session):
        from metadata_store.repository import MetadataRepository
        repo = MetadataRepository(db_session)
        user = await repo.get_user("nonexistent")
        assert user is None


@pytest.mark.asyncio
class TestRepositoryDatasetProfiles:
    async def test_upsert_creates_new(self, db_session):
        from metadata_store.repository import MetadataRepository
        repo = MetadataRepository(db_session)
        profile = await repo.upsert_dataset_profile(
            file_id="sales.csv",
            dataset_name="Sales Data",
            profile={"null_rate": 0.02, "row_count": 1000},
            rows_count=1000,
            columns_count=5,
        )
        assert profile.file_id == "sales.csv"
        assert profile.dataset_name == "Sales Data"
        assert profile.rows_count == 1000
        assert profile.profile["null_rate"] == 0.02

    async def test_upsert_updates_existing(self, db_session):
        from metadata_store.repository import MetadataRepository
        repo = MetadataRepository(db_session)
        await repo.upsert_dataset_profile(
            file_id="sales.csv", dataset_name="V1", profile={}, rows_count=100,
        )
        updated = await repo.upsert_dataset_profile(
            file_id="sales.csv", dataset_name="V2", profile={"version": 2}, rows_count=200,
        )
        assert updated.dataset_name == "V2"
        assert updated.rows_count == 200
        assert updated.profile == {"version": 2}

    async def test_get_not_found(self, db_session):
        from metadata_store.repository import MetadataRepository
        repo = MetadataRepository(db_session)
        result = await repo.get_dataset_profile("missing.csv")
        assert result is None

    async def test_get_found(self, db_session):
        from metadata_store.repository import MetadataRepository
        repo = MetadataRepository(db_session)
        await repo.upsert_dataset_profile(
            file_id="data.parquet", dataset_name="Data", profile={},
        )
        result = await repo.get_dataset_profile("data.parquet")
        assert result is not None
        assert result.dataset_name == "Data"


@pytest.mark.asyncio
class TestRepositoryDocuments:
    async def test_upsert_creates_document(self, db_session):
        from metadata_store.repository import MetadataRepository
        repo = MetadataRepository(db_session)
        doc = await repo.upsert_document(
            title="Sales Schema",
            body="columns: id, amount, date",
            tags=["schema", "sales"],
        )
        assert doc.title == "Sales Schema"
        assert doc.tags == ["schema", "sales"]
        assert doc.source_type == "schema"
        assert doc.id is not None

    async def test_upsert_updates_existing_document(self, db_session):
        from metadata_store.repository import MetadataRepository
        repo = MetadataRepository(db_session)
        await repo.upsert_document(document_id="doc1", title="V1", body="old")
        updated = await repo.upsert_document(document_id="doc1", title="V2", body="new")
        assert updated.title == "V2"
        assert updated.body == "new"

    async def test_upsert_with_embedding(self, db_session):
        from metadata_store.repository import MetadataRepository
        repo = MetadataRepository(db_session)
        doc = await repo.upsert_document(
            document_id="doc-emb",
            title="Embedded Doc",
            body="some text",
            embedding=[3.0, 4.0],
        )
        assert doc.embedding is not None
        assert doc.embedding.embedding_model == "hash-projection"
        # _normalize_vector turns [3, 4] into [0.6, 0.8] (unit vector)
        assert abs(doc.embedding.vector[0] - 0.6) < 1e-5
        assert abs(doc.embedding.vector[1] - 0.8) < 1e-5

    async def test_list_embeddings(self, db_session):
        from metadata_store.repository import MetadataRepository
        repo = MetadataRepository(db_session)
        await repo.upsert_document(
            document_id="d1", title="A", body="a", embedding=[1.0, 0.0],
        )
        await repo.upsert_document(
            document_id="d2", title="B", body="b", embedding=[0.0, 1.0],
        )
        await repo.upsert_document(
            document_id="d3", title="C", body="c",
        )
        embeddings = await repo.list_embeddings()
        assert len(embeddings) == 2
        assert all(e.document is not None for e in embeddings)


@pytest.mark.asyncio
class TestRepositorySemanticModels:
    async def test_upsert_creates_model_with_fields(self, db_session):
        from metadata_store.repository import MetadataRepository
        repo = MetadataRepository(db_session)
        model = await repo.upsert_semantic_model(
            model_id=None,
            name="Revenue Model",
            description="Tracks revenue metrics",
            source={"table": "sales"},
            tags=["finance"],
            fields=[
                {"name": "revenue", "field_type": "measure", "data_type": "DOUBLE",
                 "expression": "SUM(amount)", "aggregation": "sum"},
                {"name": "region", "field_type": "dimension", "data_type": "VARCHAR"},
            ],
        )
        assert model.name == "Revenue Model"
        assert model.tags == ["finance"]
        assert len(model.fields) == 2
        assert model.fields[0].name == "revenue"

    async def test_upsert_updates_fields(self, db_session):
        from metadata_store.repository import MetadataRepository
        repo = MetadataRepository(db_session)
        model = await repo.upsert_semantic_model(
            model_id="sm1", name="V1", description=None,
            source={}, fields=[{"name": "a", "field_type": "dimension"}],
        )
        assert len(model.fields) == 1

        updated = await repo.upsert_semantic_model(
            model_id="sm1", name="V2", description="updated",
            source={}, fields=[
                {"name": "b", "field_type": "measure"},
                {"name": "c", "field_type": "dimension"},
            ],
        )
        assert updated.name == "V2"
        assert len(updated.fields) == 2
        assert updated.fields[0].name == "b"

    async def test_upsert_creates_model_without_fields(self, db_session):
        from metadata_store.repository import MetadataRepository
        repo = MetadataRepository(db_session)
        model = await repo.upsert_semantic_model(
            model_id=None,
            name="Revenue Model",
            description="Tracks revenue metrics",
            source={"table": "sales"},
            tags=["finance"],
        )
        assert model.name == "Revenue Model"
        assert model.tags == ["finance"]
        assert model.id is not None

    async def test_upsert_updates_scalar_fields(self, db_session):
        from metadata_store.repository import MetadataRepository
        repo = MetadataRepository(db_session)
        await repo.upsert_semantic_model(
            model_id="sm1", name="V1", description=None, source={},
        )
        updated = await repo.upsert_semantic_model(
            model_id="sm1", name="V2", description="updated",
            source={"table": "orders"},
        )
        assert updated.name == "V2"
        assert updated.description == "updated"
        assert updated.source == {"table": "orders"}

    async def test_list_models(self, db_session):
        from metadata_store.repository import MetadataRepository
        repo = MetadataRepository(db_session)
        await repo.upsert_semantic_model(
            model_id="m1", name="Model A", description=None, source={},
        )
        await repo.upsert_semantic_model(
            model_id="m2", name="Model B", description=None, source={},
        )
        models = await repo.list_semantic_models()
        assert len(models) == 2

    async def test_get_model_found(self, db_session):
        from metadata_store.repository import MetadataRepository
        repo = MetadataRepository(db_session)
        await repo.upsert_semantic_model(
            model_id="m1", name="Target", description=None, source={},
        )
        model = await repo.get_semantic_model("m1")
        assert model is not None
        assert model.name == "Target"

    async def test_get_model_not_found(self, db_session):
        from metadata_store.repository import MetadataRepository
        repo = MetadataRepository(db_session)
        model = await repo.get_semantic_model("nonexistent")
        assert model is None
