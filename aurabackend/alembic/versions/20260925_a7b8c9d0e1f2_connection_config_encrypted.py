"""gateway_connections.config_encrypted — connector-specific settings, encrypted at rest

BUG-170: BigQuery's project/credentials JSON and FAISS's dimension/index type had
nowhere to live, so those connectors could not be created. This adds ONE nullable
Text column holding a Fernet token (shared/credentials.py) over a JSON object of
those settings, written by the same repository path as `password_encrypted`.

Additive and nullable on purpose: existing rows read as "no extra settings", the
column has no default to backfill, and the downgrade simply drops it.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-09-25 00:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('gateway_connections', sa.Column('config_encrypted', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('gateway_connections', 'config_encrypted')
