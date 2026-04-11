from __future__ import annotations

import hashlib
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List

import numpy as np
from fastapi import Body, Depends, FastAPI, HTTPException, Query

# Add parent directory to path
from metadata_store.db import init_db
from metadata_store.repository import MetadataRepository, get_repository


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
	await init_db()
	yield


kb_app = FastAPI(title="AURA Knowledge Base Service", lifespan=_lifespan)


@kb_app.get("/health")
async def health():
	return {"status": "healthy", "service": "knowledge_base"}


EMBEDDING_DIM = 256


def _embed_text(text: str) -> List[float]:
	digest = hashlib.sha256(text.encode("utf-8")).digest()
	base = np.frombuffer(digest, dtype=np.uint8).astype(np.float32)
	repeats = (EMBEDDING_DIM // base.size) + 1
	tiled = np.tile(base, repeats)[:EMBEDDING_DIM]
	norm = np.linalg.norm(tiled)
	if norm == 0:
		return tiled.tolist()
	return (tiled / norm).tolist()


def _score_similarity(a: List[float], b: List[float]) -> float:
	vec_a = np.array(a, dtype=np.float32)
	vec_b = np.array(b, dtype=np.float32)
	if vec_a.size == 0 or vec_b.size == 0:
		return 0.0
	return float(np.dot(vec_a, vec_b))


async def _search_documents_internal(
	*,
	query: str,
	limit: int,
	repo: MetadataRepository,
) -> List[Dict[str, Any]]:
	query_vector = _embed_text(query)
	embeddings = await repo.list_embeddings()
	scored: List[tuple[float, Any]] = []
	for embedding in embeddings:
		document = getattr(embedding, "document", None)
		if embedding.vector and document:
			scored.append((_score_similarity(query_vector, embedding.vector), document))

	scored.sort(key=lambda item: item[0], reverse=True)
	results: List[Dict[str, Any]] = []
	for score, document in scored[:limit]:
		results.append(
			{
				"document_id": document.id,
				"title": document.title,
				"score": float(score),
				"summary": document.body[:240],
				"tags": document.tags,
				"details": document.details,
			}
		)
	return results


@kb_app.post("/ingest")
async def ingest_document(
	payload: Dict[str, Any] = Body(...),
	repo: MetadataRepository = Depends(get_repository),
) -> Dict[str, Any]:
	title = payload.get("title")
	body = payload.get("body")
	if not title or not body:
		raise HTTPException(status_code=400, detail="Both 'title' and 'body' are required")

	embedding = _embed_text(body)
	document = await repo.upsert_document(
		document_id=payload.get("id"),
		title=title,
		body=body,
		tags=payload.get("tags") or [],
		details=payload.get("details") or {},
		source_type=payload.get("source_type", "schema"),
		embedding=embedding,
		embedding_model="sha256-projection",
	)
	return {
		"document": {
			"id": document.id,
			"title": document.title,
			"tags": document.tags,
			"source_type": document.source_type,
		}
	}


@kb_app.get("/search")
async def search_documents(
	q: str = Query(..., description="Search query for documentation or schema"),
	limit: int = Query(5, ge=1, le=20),
	repo: MetadataRepository = Depends(get_repository),
) -> Dict[str, Any]:
	results = await _search_documents_internal(query=q, limit=limit, repo=repo)
	return {"query": q, "results": results}


@kb_app.get("/schemas/{table_name}")
async def get_schema(table_name: str, repo: MetadataRepository = Depends(get_repository)) -> Dict[str, Any]:
	results = await _search_documents_internal(query=table_name, limit=1, repo=repo)
	if not results:
		raise HTTPException(status_code=404, detail="No schema documentation found")
	top = results[0]
	return {
		"table": table_name,
		"documentation": top,
	}
