from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict

from fastapi import Body, Depends, FastAPI, HTTPException

from shared.config import settings
from shared.logging_config import get_logger
from shared.service_factory import create_service

from .db import get_session, init_db
from .models import User
from .repository import MetadataRepository, get_repository

logger = get_logger("aura.metadata_store")


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
	logger.info("Starting Metadata Store — initializing DB")
	await init_db()
	if settings.admin_email:
		async for session in get_session():
			repo = MetadataRepository(session)
			await repo.upsert_user("admin", name="AURA Admin", email=settings.admin_email)
			break
	yield
	logger.info("Shutting down Metadata Store")


metadata_app = create_service(
	name="Metadata Store",
	service_tag="metadata_store",
	lifespan=_lifespan,
)


def _serialize_user(user: User) -> Dict[str, Any]:
	return {
		"id": user.id,
		"name": user.name,
		"email": user.email,
		"created_at": user.created_at.isoformat() if user.created_at else None,
		"updated_at": user.updated_at.isoformat() if user.updated_at else None,
	}


@metadata_app.get("/users/{user_id}")
async def get_user(user_id: str, repo: MetadataRepository = Depends(get_repository)) -> Dict[str, Any]:
	user = await repo.get_user(user_id)
	if user is None:
		raise HTTPException(status_code=404, detail="User not found")
	return {"user": _serialize_user(user)}


@metadata_app.post("/users/{user_id}")
async def upsert_user(
	user_id: str,
	payload: Dict[str, Any] = Body(...),
	repo: MetadataRepository = Depends(get_repository),
) -> Dict[str, Any]:
	user = await repo.upsert_user(user_id, name=payload.get("name", user_id), email=payload.get("email"))
	return {"user": _serialize_user(user)}


# ==================== Dataset Profiles ====================

def _serialize_dataset_profile(profile) -> Dict[str, Any]:
	return {
		"id": profile.id,
		"file_id": profile.file_id,
		"dataset_name": profile.dataset_name,
		"profile": profile.profile,
		"rows_count": profile.rows_count,
		"columns_count": profile.columns_count,
		"created_at": profile.created_at.isoformat() if profile.created_at else None,
		"updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
	}


@metadata_app.get("/dataset-profiles/{file_id}")
async def get_dataset_profile(
	file_id: str,
	repo: MetadataRepository = Depends(get_repository),
) -> Dict[str, Any]:
	"""Retrieve a dataset profile by file ID."""
	profile = await repo.get_dataset_profile(file_id)
	if profile is None:
		raise HTTPException(status_code=404, detail="Dataset profile not found")
	return {"profile": _serialize_dataset_profile(profile)}


@metadata_app.post("/dataset-profiles/{file_id}")
async def upsert_dataset_profile(
	file_id: str,
	payload: Dict[str, Any] = Body(...),
	repo: MetadataRepository = Depends(get_repository),
) -> Dict[str, Any]:
	"""Create or update a dataset profile."""
	profile = await repo.upsert_dataset_profile(
		file_id=file_id,
		dataset_name=payload.get("dataset_name"),
		profile=payload.get("profile", {}),
		rows_count=payload.get("rows_count"),
		columns_count=payload.get("columns_count"),
	)
	return {"profile": _serialize_dataset_profile(profile)}


# ==================== Semantic Models ====================

def _serialize_semantic_model(model) -> Dict[str, Any]:
	return {
		"id": model.id,
		"name": model.name,
		"description": model.description,
		"source": model.source,
		"tags": model.tags,
		"fields": [
			{
				"id": f.id,
				"name": f.name,
				"field_type": f.field_type,
				"data_type": f.data_type,
				"expression": f.expression,
				"description": f.description,
				"aggregation": f.aggregation,
				"metadata": f.field_metadata,
			}
			for f in (model.fields or [])
		],
		"created_at": model.created_at.isoformat() if model.created_at else None,
		"updated_at": model.updated_at.isoformat() if model.updated_at else None,
	}


@metadata_app.get("/semantic-models")
async def list_semantic_models(
	repo: MetadataRepository = Depends(get_repository),
) -> Dict[str, Any]:
	"""List all semantic models."""
	models = await repo.list_semantic_models()
	return {"models": [_serialize_semantic_model(m) for m in models], "count": len(models)}


@metadata_app.get("/semantic-models/{model_id}")
async def get_semantic_model(
	model_id: str,
	repo: MetadataRepository = Depends(get_repository),
) -> Dict[str, Any]:
	"""Get a semantic model by ID."""
	model = await repo.get_semantic_model(model_id)
	if model is None:
		raise HTTPException(status_code=404, detail="Semantic model not found")
	return {"model": _serialize_semantic_model(model)}


@metadata_app.post("/semantic-models")
async def upsert_semantic_model(
	payload: Dict[str, Any] = Body(...),
	repo: MetadataRepository = Depends(get_repository),
) -> Dict[str, Any]:
	"""Create or update a semantic model."""
	if not payload.get("name"):
		raise HTTPException(status_code=400, detail="'name' is required")
	model = await repo.upsert_semantic_model(
		model_id=payload.get("id"),
		name=payload["name"],
		description=payload.get("description"),
		source=payload.get("source", {}),
		tags=payload.get("tags"),
		fields=payload.get("fields"),
	)
	return {"model": _serialize_semantic_model(model)}


# ==================== Data Sources ====================

@metadata_app.get("/data-sources")
async def list_data_sources(
	repo: MetadataRepository = Depends(get_repository),
) -> Dict[str, Any]:
	"""List all registered data sources."""
	sources = await repo.list_data_sources()
	return {
		"data_sources": [
			{
				"id": s.id,
				"name": s.name,
				"type": s.type,
				"connection_id": s.connection_id,
				"details": s.details,
				"created_at": s.created_at.isoformat() if s.created_at else None,
			}
			for s in sources
		],
		"count": len(sources),
	}
