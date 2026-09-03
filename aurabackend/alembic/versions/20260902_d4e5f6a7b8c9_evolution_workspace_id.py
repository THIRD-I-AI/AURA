"""add workspace_id to the four evolution tables — close the cross-tenant read

``GET /evolution/patterns``, ``/proposals``, ``/proposals/{id}``, ``/log`` and
``/feedback/summary`` all ran a bare ``select(...)`` with no tenant filter, and
none of those routes even accepted a ``Request`` to scope from -- any
authenticated caller could see every other tenant's prompts, agent output,
proposal rationale and pattern stats. ``POST /evolution/feedback`` also wrote
rows with no owning tenant at all, so a ``session_id`` was never actually tied
to the caller who recorded it.

Same shape as ``20260826_c3e4f5a6b7c8_semantic_models_org_id.py`` (see that
migration's docstring for the full rationale), applied to all four tables in
one migration since this is a single self-contained subsystem rather than a
scattered sweep:

  * ``evolution_execution_patterns``
  * ``evolution_improvement_proposals``
  * ``evolution_system_log``
  * ``evolution_agent_feedback``

Nullable on purpose, for the same reason as the semantic_models migration: a
row written before tenanting (or by the engine's own background cycle, which
has no request-bound tenant) gets ``workspace_id IS NULL``. The read path
treats NULL as "not mine", so pre-existing rows become invisible rather than
universally visible -- the safe direction to fail.

Revision ID: d4e5f6a7b8c9
Revises: c3e4f5a6b7c8
Create Date: 2026-09-02 00:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3e4f5a6b7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = (
    'evolution_execution_patterns',
    'evolution_improvement_proposals',
    'evolution_system_log',
    'evolution_agent_feedback',
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column('workspace_id', sa.String(length=64), nullable=True),
        )
        # Indexed because every read now filters on it.
        op.create_index(
            f'ix_{table}_workspace_id', table, ['workspace_id'], unique=False,
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_index(f'ix_{table}_workspace_id', table_name=table)
        op.drop_column(table, 'workspace_id')
