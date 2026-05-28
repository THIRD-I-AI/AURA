from __future__ import annotations

import uuid
from typing import Any, AsyncGenerator, Dict, Iterable, List, Optional

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .db import get_session
from .models import (
    DatasetProfile,
    DataSource,
    Document,
    DocumentEmbedding,
    SemanticField,
    SemanticModel,
    User,
)


def _normalize_vector(values: Iterable[float]) -> List[float]:
    array = np.array(list(values), dtype=np.float32)
    if np.allclose(array, 0.0):
        return array.tolist()
    normalized = array / np.linalg.norm(array)
    return normalized.tolist()


class MetadataRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_user(self, user_id: str) -> Optional[User]:
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def upsert_user(self, user_id: str, name: str, email: Optional[str] = None) -> User:
        user = await self.get_user(user_id)
        if user is None:
            user = User(id=user_id, name=name, email=email)
            self._session.add(user)
        else:
            user.name = name
            user.email = email
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def list_data_sources(self) -> List[DataSource]:
        result = await self._session.execute(select(DataSource))
        return list(result.scalars().all())

    async def upsert_document(
        self,
        *,
        document_id: Optional[str] = None,
        title: str,
        body: str,
        tags: Optional[List[str]] = None,
    details: Optional[dict[str, Any]] = None,
        source_type: str = "schema",
        embedding: Optional[Iterable[float]] = None,
        embedding_model: str = "hash-projection",
    ) -> Document:
        document_id = document_id or str(uuid.uuid4())
        # selectinload(.embedding) eagerly populates the relationship
        # so the update path below can mutate it without triggering a
        # MissingGreenlet lazy-load under async sessions.
        result = await self._session.execute(
            select(Document)
            .options(selectinload(Document.embedding))
            .where(Document.id == document_id)
        )
        document = result.scalar_one_or_none()

        if document is None:
            # NEW path. Construct the Document WITH its embedding pre-
            # populated so SQLAlchemy never has to read the relationship
            # from the DB. Assigning `document.embedding = ...` after
            # add() would trigger a lazy-load to detect the old value
            # for replacement, which fails under async sessions.
            emb = None
            if embedding is not None:
                emb = DocumentEmbedding(
                    vector=_normalize_vector(embedding),
                    embedding_model=embedding_model,
                )
            document = Document(
                id=document_id,
                title=title,
                body=body,
                tags=tags or [],
                details=details or {},
                source_type=source_type,
                embedding=emb,
            )
            self._session.add(document)
        else:
            # UPDATE path. selectinload above guarantees document.embedding
            # is preloaded, so accessing / mutating / reassigning is safe.
            document.title = title
            document.body = body
            document.tags = tags or []
            document.details = details or {}
            document.source_type = source_type
            if embedding is not None:
                vector = _normalize_vector(embedding)
                if document.embedding is not None:
                    document.embedding.vector = vector
                    document.embedding.embedding_model = embedding_model
                else:
                    document.embedding = DocumentEmbedding(
                        vector=vector,
                        embedding_model=embedding_model,
                    )

        await self._session.commit()
        await self._session.refresh(document, ["embedding"])
        return document

    async def list_embeddings(self) -> List[DocumentEmbedding]:
        stmt = select(DocumentEmbedding).options(selectinload(DocumentEmbedding.document))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def upsert_dataset_profile(
        self,
        *,
        file_id: str,
        dataset_name: Optional[str],
        profile: Dict[str, Any],
        rows_count: Optional[int] = None,
        columns_count: Optional[int] = None,
    ) -> DatasetProfile:
        profile_id = file_id
        existing = await self._session.get(DatasetProfile, profile_id)
        if existing is None:
            existing = DatasetProfile(
                id=profile_id,
                file_id=file_id,
                dataset_name=dataset_name,
                profile=profile,
                rows_count=rows_count,
                columns_count=columns_count,
            )
            self._session.add(existing)
        else:
            existing.dataset_name = dataset_name
            existing.profile = profile
            existing.rows_count = rows_count
            existing.columns_count = columns_count

        await self._session.commit()
        await self._session.refresh(existing)
        return existing

    async def get_dataset_profile(self, file_id: str) -> Optional[DatasetProfile]:
        result = await self._session.execute(select(DatasetProfile).where(DatasetProfile.file_id == file_id))
        return result.scalar_one_or_none()

    async def upsert_semantic_model(
        self,
        *,
        model_id: Optional[str],
        name: str,
        description: Optional[str],
        source: Dict[str, Any],
        tags: Optional[List[str]] = None,
        fields: Optional[List[Dict[str, Any]]] = None,
    ) -> SemanticModel:
        model_id = model_id or str(uuid.uuid4())

        def _build_fields(specs: List[Dict[str, Any]]) -> List[SemanticField]:
            return [
                SemanticField(
                    id=f.get("id") or str(uuid.uuid4()),
                    name=f["name"],
                    field_type=f.get("field_type", "dimension"),
                    data_type=f.get("data_type"),
                    expression=f.get("expression"),
                    description=f.get("description"),
                    aggregation=f.get("aggregation"),
                    metadata=f.get("metadata", {}),
                )
                for f in specs
            ]

        # selectinload(.fields) eagerly populates the collection so the
        # update path can mutate it without triggering a lazy-load
        # MissingGreenlet error under async sessions.
        result = await self._session.execute(
            select(SemanticModel)
            .options(selectinload(SemanticModel.fields))
            .where(SemanticModel.id == model_id)
        )
        model = result.scalar_one_or_none()

        if model is None:
            # NEW path. Construct the model WITH its fields pre-populated
            # so SQLAlchemy never has to read the relationship from the
            # DB. Calling model.fields.clear() / .append() after add()
            # would trigger a lazy-load to read the existing collection,
            # which fails under async sessions.
            initial_fields = _build_fields(fields) if fields is not None else []
            model = SemanticModel(
                id=model_id,
                name=name,
                description=description,
                source=source,
                tags=tags or [],
                fields=initial_fields,
            )
            self._session.add(model)
        else:
            # UPDATE path. selectinload above guarantees model.fields is
            # preloaded, so mutation is safe.
            model.name = name
            model.description = description
            model.source = source
            model.tags = tags or []
            if fields is not None:
                model.fields.clear()
                model.fields.extend(_build_fields(fields))

        await self._session.commit()
        await self._session.refresh(model, ["fields"])
        return model

    async def list_semantic_models(self) -> List[SemanticModel]:
        stmt = select(SemanticModel).options(selectinload(SemanticModel.fields))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_semantic_model(self, model_id: str) -> Optional[SemanticModel]:
        stmt = select(SemanticModel).options(selectinload(SemanticModel.fields)).where(SemanticModel.id == model_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


async def get_repository() -> AsyncGenerator[MetadataRepository, None]:
    async for session in get_session():
        repo = MetadataRepository(session)
        try:
            yield repo
        finally:
            await session.close()
        break
