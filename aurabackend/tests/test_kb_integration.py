"""
Sprint S34 — Knowledge Base end-to-end integration tests.

Tier A (pure Python, no optional deps beyond numpy).

Covers the search side of the KB flow against persisted documents:
  * _search_documents_internal walks list_embeddings, scores against
    the query vector, ranks by similarity
  * Top-K limit honoured
  * Documents without embeddings are excluded from search
  * Empty corpus is handled
  * Result payload shape (title, score, summary, tags, details)

This crosses knowledge_base ↔ metadata_store ↔ embedding pipeline.
The write path (MetadataRepository.upsert_document) has a known async
lazy-load issue (surfaced in S31a) and is therefore bypassed here in
favour of direct SQLAlchemy inserts — the test focuses on the search
contract that the kb_app serves.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from knowledge_base.main import _embed_text, _search_documents_internal
from metadata_store.db import Base
from metadata_store.models import Document, DocumentEmbedding
from metadata_store.repository import MetadataRepository, _normalize_vector


@pytest.fixture
async def repo():
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield MetadataRepository(session)

    await engine.dispose()


async def _insert(repo, doc_id, title, body, *, with_embedding=True):
    """Insert a Document + (optional) DocumentEmbedding directly via
    SQLAlchemy. Bypasses upsert_document to avoid the async lazy-load
    bug in the relationship accessor."""
    session = repo._session
    doc = Document(
        id=doc_id, title=title, body=body,
        tags=[], details={}, source_type="schema",
    )
    session.add(doc)
    await session.flush()
    if with_embedding:
        vec = _normalize_vector(_embed_text(body))
        emb = DocumentEmbedding(
            document_id=doc_id, vector=vec,
            embedding_model="sha256-projection",
        )
        session.add(emb)
    await session.commit()


# ── Search ranking ────────────────────────────────────────────────

@pytest.mark.asyncio
class TestSearchRanking:
    async def test_exact_text_match_ranks_first(self, repo):
        await _insert(repo, "d1", "Sales schema", "revenue and quantity by product")
        await _insert(repo, "d2", "Orders schema", "customer ship date and status")
        await _insert(repo, "d3", "Inventory schema", "warehouse stock levels")

        results = await _search_documents_internal(
            query="revenue and quantity by product",
            limit=3,
            repo=repo,
        )
        assert len(results) >= 1
        assert results[0]["document_id"] == "d1"

    async def test_limit_caps_results(self, repo):
        for i in range(5):
            await _insert(repo, f"d{i}", f"Doc {i}", f"content variant {i}")

        results = await _search_documents_internal(
            query="content", limit=2, repo=repo,
        )
        assert len(results) <= 2

    async def test_scores_descending(self, repo):
        for i in range(3):
            await _insert(repo, f"d{i}", f"Doc {i}", f"unique text variant {i}")

        results = await _search_documents_internal(
            query="unique text variant 0", limit=3, repo=repo,
        )
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)


# ── Result payload shape ──────────────────────────────────────────

@pytest.mark.asyncio
class TestResultPayload:
    async def test_includes_required_fields(self, repo):
        await _insert(repo, "d1", "My Title", "body content goes here for indexing")
        results = await _search_documents_internal(
            query="body content", limit=1, repo=repo,
        )
        assert len(results) == 1
        r = results[0]
        assert r["document_id"] == "d1"
        assert r["title"] == "My Title"
        assert "score" in r
        assert "summary" in r
        assert r["tags"] == []
        assert r["details"] == {}

    async def test_summary_truncated_to_240(self, repo):
        long_body = "x" * 500
        await _insert(repo, "d1", "T", long_body)
        results = await _search_documents_internal(
            query=long_body, limit=1, repo=repo,
        )
        assert len(results[0]["summary"]) == 240


# ── Documents without embeddings ──────────────────────────────────

@pytest.mark.asyncio
class TestEmbeddingFiltering:
    async def test_doc_without_embedding_excluded(self, repo):
        await _insert(repo, "d1", "With embedding", "the searchable one")
        await _insert(repo, "d2", "No embedding", "this document has no vector",
                      with_embedding=False)

        results = await _search_documents_internal(
            query="searchable", limit=5, repo=repo,
        )
        doc_ids = {r["document_id"] for r in results}
        assert "d1" in doc_ids
        assert "d2" not in doc_ids


# ── Empty corpus ──────────────────────────────────────────────────

@pytest.mark.asyncio
class TestEmptyCorpus:
    async def test_search_with_no_documents_returns_empty(self, repo):
        results = await _search_documents_internal(
            query="anything", limit=10, repo=repo,
        )
        assert results == []
