from __future__ import annotations

import os
from typing import Any, Dict

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import Body, Depends, FastAPI, HTTPException

from .db import get_session, init_db
from .models import User
from .repository import MetadataRepository, get_repository


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
	await init_db()
	seed_admin = os.getenv("AURA_ADMIN_EMAIL")
	if seed_admin:
		async for session in get_session():
			repo = MetadataRepository(session)
			await repo.upsert_user("admin", name="AURA Admin", email=seed_admin)
			break
	yield


metadata_app = FastAPI(title="AURA Metadata Store", lifespan=_lifespan)


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
