"""add org_id tenant key to users

The tenant boundary is ``users.org_id``: it is minted at registration,
carried in the AURA JWT, and every downstream service scopes its data on
the org_id *string* from the verified token (via ``shared.auth.require_tenant``),
never a foreign-key column. The ORM (``metadata_store.models.User``) has
declared this column, but no migration created it — so a fresh
``alembic upgrade head`` built ``users`` without it and login/registration
crashed on ``user.org_id``. This migration closes that drift.

Nullable by design: pre-existing users predate multi-tenancy; the auth layer
falls back to the user id as the tenant when org_id is absent
(``user.org_id or user.id``), so no backfill is required for isolation.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-04-30 07:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e3f4a5b6c7d8'
down_revision: Union[str, None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('org_id', sa.String(length=64), nullable=True))
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_org_id'), ['org_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_org_id'))
    op.drop_column('users', 'org_id')
