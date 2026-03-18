from __future__ import annotations

import os
import sys
from typing import Any, Dict

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import Body, Depends, FastAPI, HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.service_factory import create_service
from shared.config import settings
from shared.logging_config import get_logger
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
