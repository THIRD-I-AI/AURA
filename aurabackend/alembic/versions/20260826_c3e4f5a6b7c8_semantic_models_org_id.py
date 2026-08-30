"""add workspace_id to semantic_models — close the cross-tenant read

``GET /semantic/models`` returned EVERY tenant's semantic models. The repository
method was a bare ``select(SemanticModel)`` with no filter, and none of the four
/semantic/* routes even accepted a Request, so they could not scope in
principle. A semantic model carries field names, expressions and descriptions
derived from the owning tenant's data, so this leaked modelled business logic,
not merely row counts.

The root cause is broader than one route: of the tables in
metadata_store/models.py only ``users`` has a tenant column at all
(``DataSource``, ``Document``, ``DocumentEmbedding``, ``SchemaColumn``,
``DARInsight``, ``DatasetProfile``, ``SemanticModel``, ``SemanticField`` have
none). This migration closes the one with live, unscoped read routes; the rest
are tracked separately rather than swept into one large migration.

``semantic_fields`` deliberately gets no column. It is reachable only through
its parent model's foreign key, so scoping the parent scopes the children --
a second denormalised tenant column would create two sources of truth that can
disagree.

Nullable on purpose. Rows written before tenanting have workspace_id NULL; NOT NULL
would either fail the migration on a live database or force a guessed backfill
that silently assigns one tenant's data to another. The read path treats NULL as
"not mine", so pre-existing rows become invisible rather than universally
visible -- the safe direction to fail.

Revision ID: c3e4f5a6b7c8
Revises: b2d3e4f5a6b7
Create Date: 2026-08-26 00:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3e4f5a6b7c8'
down_revision: Union[str, None] = 'b2d3e4f5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'semantic_models',
        sa.Column('workspace_id', sa.String(length=64), nullable=True),
    )
    # Indexed because every read now filters on it.
    op.create_index(
        'ix_semantic_models_workspace_id', 'semantic_models', ['workspace_id'], unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_semantic_models_workspace_id', table_name='semantic_models')
    op.drop_column('semantic_models', 'workspace_id')
