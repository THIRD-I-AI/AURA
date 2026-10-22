"""gateway_workspaces -- workspace folder records stored in the database

BUG-494: the workspaces router kept its registry in one worker's memory, so with
several gateway workers a new workspace was visible on only the worker that made it
(list missing it, PATCH/DELETE 404 elsewhere), and every restart deleted all of them.

Additive: a new table and its index, nothing existing changes; the downgrade drops it.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-10-22 00:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'gateway_workspaces',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('tenant_id', sa.String(length=255), nullable=True),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.String(length=64), nullable=False),
        sa.Column('updated_at', sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_gateway_workspaces_tenant_id'), 'gateway_workspaces', ['tenant_id'], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_gateway_workspaces_tenant_id'), table_name='gateway_workspaces')
    op.drop_table('gateway_workspaces')
