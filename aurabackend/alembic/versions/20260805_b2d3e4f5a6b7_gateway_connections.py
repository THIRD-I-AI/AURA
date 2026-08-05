"""gateway_connections — tenant-scoped connections with encrypted credentials

Connections lived in a module-level dict with no tenant filter, so
GET /connections returned every organisation's connections (host, port,
database, username) to any caller. The password supplied at creation was also
discarded outright, so no stored connection could ever authenticate.

Revision ID: b2d3e4f5a6b7
Revises: a1c2d3e4f5a6
Create Date: 2026-08-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b2d3e4f5a6b7"
down_revision = "a1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Guarded like its predecessors: create_all in the gateway lifespan may
    # already have produced this table on a dev database, and an unguarded
    # CREATE would abort the whole upgrade there.
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if "gateway_connections" in existing:
        return

    op.create_table(
        "gateway_connections",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("host", sa.String(255), nullable=True),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("database", sa.String(255), nullable=True),
        sa.Column("username", sa.String(255), nullable=True),
        # Fernet token from shared/credentials.py — never plaintext. Nullable
        # because a connector may authenticate by other means (e.g. BigQuery
        # service-account credentials) or be created without one.
        sa.Column("password_encrypted", sa.Text(), nullable=True),
        sa.Column("ssl", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_tested", sa.String(64), nullable=True),
        sa.Column("table_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.String(64), nullable=False),
        sa.Column("created_ts", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.String(64), nullable=False),
    )
    op.create_index(
        "ix_connections_ws_created",
        "gateway_connections",
        ["workspace_id", sa.text("created_ts DESC")],
    )


def downgrade() -> None:
    # Destructive: drops every stored connection, including its encrypted
    # credential. Deliberately not softened — a schema without this table has
    # nowhere to keep them, and leaving encrypted secrets behind in an
    # orphaned table would be worse.
    op.drop_index("ix_connections_ws_created", table_name="gateway_connections")
    op.drop_table("gateway_connections")
