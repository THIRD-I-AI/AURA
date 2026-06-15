from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="user")
    # SaaS Phase 1 — the tenant boundary. Every user belongs to exactly one
    # org; data isolation is enforced on org_id derived from the JWT, never a
    # client header. Nullable for pre-migration rows (they fall back to id).
    org_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(64))
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    details: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    tags: Mapped[List[str]] = mapped_column(JSON, default=list)
    source_type: Mapped[str] = mapped_column(String(64), default="schema")
    details: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    embedding: Mapped["DocumentEmbedding"] = relationship(
        "DocumentEmbedding", back_populates="document", cascade="all, delete-orphan", uselist=False
    )


class DocumentEmbedding(Base):
    __tablename__ = "document_embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(String(64), ForeignKey("documents.id", ondelete="CASCADE"))
    vector: Mapped[List[float]] = mapped_column(JSON)
    embedding_model: Mapped[str] = mapped_column(String(128), default="hash-projection")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    document: Mapped[Document] = relationship("Document", back_populates="embedding")


class SchemaColumn(Base):
    """One row per (source, table, column). Populated by the upload-path
    indexer (``shared.schema_indexer``) so the MCP server can answer
    ``metadata.search_columns`` against structured rows instead of
    LIKE-grepping a free-text ``documents.body`` blob.

    Why a dedicated table: agents frequently ask "which sources have a
    column named like X?" before deciding which schema to load into the
    prompt. A LIKE search over a markdown-ish body returns matched
    documents; this table returns the matched columns themselves
    (source_id + table + column + dtype + samples), which is the slice
    that can actually go into the SQL-gen prompt.
    """
    __tablename__ = "schema_columns"
    __table_args__ = (
        UniqueConstraint("source_id", "table_name", "column_name",
                         name="uq_schema_column"),
        Index("ix_schema_column_lower_name", "column_name_lower"),
        Index("ix_schema_column_table", "source_id", "table_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # source_id is the data_sources row id when an upload is associated
    # with a registered source, OR the upload filename stem when the
    # upload is ad-hoc (the chat path's typical case).
    source_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    column_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Lower-cased duplicate so the LIKE-search index is case-insensitive
    # without forcing functional indexes (which Postgres supports but
    # SQLite does not — keeping this dialect-portable matters here).
    column_name_lower: Mapped[str] = mapped_column(String(255), nullable=False)
    data_type: Mapped[str] = mapped_column(String(64), nullable=False)
    is_nullable: Mapped[bool] = mapped_column(Boolean, default=True)
    ordinal_position: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sample_values: Mapped[List[Any]] = mapped_column(JSON, default=list)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[List[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class DARInsight(Base):
    """One row per finding emitted by the Data Agnostic Researcher.

    Populated headlessly by ``dar_service`` running its LangGraph DAG
    over the shared DuckDB analytics lake — no human prompt. Each row
    captures a question DAR asked itself, the SQL it ran to answer it,
    and the LLM-scored finding (anomaly / trend / correlation /
    summary) with an importance score 0..1 that the UI can sort on.
    """
    __tablename__ = "dar_insights"
    __table_args__ = (
        Index("ix_dar_insights_source_table", "source_id", "table_name"),
        Index("ix_dar_insights_score", "score"),
        Index("ix_dar_insights_finding_type", "finding_type"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    sql_query: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    finding_type: Mapped[str] = mapped_column(String(32), nullable=False, default="summary")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    score: Mapped[float] = mapped_column(default=0.0, nullable=False)
    is_anomaly: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    run_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class DatasetProfile(Base):
    __tablename__ = "dataset_profiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    file_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    dataset_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    profile: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    rows_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    columns_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class SemanticModel(Base):
    __tablename__ = "semantic_models"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    tags: Mapped[List[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    fields: Mapped[List["SemanticField"]] = relationship(
        "SemanticField",
        back_populates="model",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class SemanticField(Base):
    __tablename__ = "semantic_fields"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(64), ForeignKey("semantic_models.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    field_type: Mapped[str] = mapped_column(String(32))  # dimension | measure
    data_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    expression: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    aggregation: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    field_metadata: Mapped[Dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    model: Mapped[SemanticModel] = relationship("SemanticModel", back_populates="fields")
