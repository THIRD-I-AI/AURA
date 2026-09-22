"""add workspace_id to dataset_profiles — close the cross-tenant collision/leak

BUG-145: DatasetProfile.id was the bare file_id, so two tenants uploading a
same-named file ("sales.csv") collided on one row -- whichever tenant's
upload upserted last silently overwrote the other's profile (including
sample data), and either tenant's GET /files/{id}/profile could read it
(that read route was gated only on file ownership, not on the profile
table itself, because the table had no tenant column to filter on -- see
c3e4f5a6b7c8's own note that DatasetProfile was one of the tables left out
of that sweep).

This closes it the same way c3e4f5a6b7c8 closed it for semantic_models:
add a nullable, indexed workspace_id column as a defense-in-depth read
filter. The write path additionally re-mints `id` as
`f"{workspace_id or 'default'}::{file_id}"` (application-side, not part of
this migration) so the two tenants' rows never share a primary key at all.

Nullable on purpose, same reasoning as c3e4f5a6b7c8: rows written before
tenanting have workspace_id NULL, and the read path treats NULL as "not
mine" rather than guessing a backfill that could assign one tenant's data
to another.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-22 00:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'dataset_profiles',
        sa.Column('workspace_id', sa.String(length=64), nullable=True),
    )
    op.create_index(
        'ix_dataset_profiles_workspace_id', 'dataset_profiles', ['workspace_id'], unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_dataset_profiles_workspace_id', table_name='dataset_profiles')
    op.drop_column('dataset_profiles', 'workspace_id')
