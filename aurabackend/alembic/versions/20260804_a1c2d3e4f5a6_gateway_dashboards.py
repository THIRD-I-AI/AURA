"""gateway_dashboards — persist dashboards instead of an in-memory list

Dashboards were held in a module-level Python list, so every dashboard a user
built disappeared on the next gateway restart, and two replicas each had their
own private copy. This table gives them the same durability the saved-query
library already had.

Revision ID: a1c2d3e4f5a6
Revises: 5e19034a50f4
Create Date: 2026-08-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1c2d3e4f5a6"
down_revision = "5e19034a50f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Guarded like the preceding migration: create_all in the gateway lifespan
    # may already have produced this table on a dev database, and a hard
    # CREATE would abort the whole upgrade there.
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if "gateway_dashboards" in existing:
        return

    op.create_table(
        "gateway_dashboards",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # Canonical JSON as TEXT, matching gateway_saved_queries.schedule_json:
        # keeps the schema portable across SQLite and Postgres, and a tile list
        # is far too small for JSONB indexing to pay for itself.
        sa.Column("tiles_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.String(64), nullable=False),
        sa.Column("created_ts", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.String(64), nullable=False),
    )
    op.create_index(
        "ix_dashboards_ws_created",
        "gateway_dashboards",
        ["workspace_id", sa.text("created_ts DESC")],
    )


def downgrade() -> None:
    # Destructive: drops every stored dashboard. Deliberately not softened —
    # a downgrade to a schema without this table has nowhere to keep them.
    op.drop_index("ix_dashboards_ws_created", table_name="gateway_dashboards")
    op.drop_table("gateway_dashboards")
