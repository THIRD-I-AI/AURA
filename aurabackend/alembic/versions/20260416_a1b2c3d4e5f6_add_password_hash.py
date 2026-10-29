"""add password_hash and role to users

Revision ID: a1b2c3d4e5f6
Revises: bb602a415b1a
Create Date: 2026-04-16 07:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'bb602a415b1a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # BUG-554: a startup create_all can already have built this from the current model
    # (see bb602a415b1a); re-adding it aborts the whole upgrade.
    if 'password_hash' in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('users')}:
        return
    op.add_column('users', sa.Column('password_hash', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('role', sa.String(length=32), server_default='user', nullable=False))


def downgrade() -> None:
    op.drop_column('users', 'role')
    op.drop_column('users', 'password_hash')
