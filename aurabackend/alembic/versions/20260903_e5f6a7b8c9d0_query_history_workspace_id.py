"""add workspace_id to gateway_query_history — close the cross-tenant read/write

``GET /query-history`` ran a bare ``select(QueryHistoryRow)`` with no tenant
filter and no ``Request`` to scope from -- any authenticated caller could
read up to 200 other tenants' executed SQL/prompts. ``POST /query-history``
likewise wrote rows with no owning tenant at all, so a fabricated entry could
be inserted with no ownership check.

Same shape as ``20260902_d4e5f6a7b8c9_evolution_workspace_id.py`` (see that
migration's docstring for the full rationale), applied to the one remaining
table in this file's schema (``gateway_query_history``) that never got a
tenant column despite its sibling ``gateway_saved_queries``
(``SavedQueryRow.workspace_id``) already having one.

Nullable on purpose, for the same reason as the evolution migration: a row
written before tenanting gets ``workspace_id IS NULL``. The read path treats
NULL as "not mine", so pre-existing rows become invisible rather than
universally visible -- the safe direction to fail. Not backfilled.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-03 00:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'gateway_query_history',
        sa.Column('workspace_id', sa.String(length=64), nullable=True),
    )
    op.create_index(
        'ix_query_history_workspace_id',
        'gateway_query_history',
        ['workspace_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_query_history_workspace_id', table_name='gateway_query_history')
    op.drop_column('gateway_query_history', 'workspace_id')
