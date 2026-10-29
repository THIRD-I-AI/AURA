"""widen dataset_profiles.id to fit the BUG-145 composite id

BUG-555: f6a7b8c9d0e1 made the id '<workspace_id or default>::<file_id>'
(workspace_id up to 64 chars, file_id up to 128) but left the column at
VARCHAR(64). SQLite ignores the length, Postgres does not: a uuid4 tenant plus a
connector source id is about 91 chars, so every profile write there failed with
StringDataRightTruncation. 255 covers the 64 + 2 + 128 = 194 worst case.

Postgres: ALTER COLUMN ... TYPE VARCHAR(255). Widening a varchar needs no table
rewrite or index rebuild, only a brief ACCESS EXCLUSIVE lock. SQLite: batch mode
copies the table, which is what SQLite needs for any column type change.

The downgrade narrows back to 64 and fails on Postgres if any id is now longer.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-10-29 00:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, None] = 'c9d0e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('dataset_profiles', schema=None) as batch_op:
        batch_op.alter_column(
            'id', existing_type=sa.String(length=64), type_=sa.String(length=255), existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table('dataset_profiles', schema=None) as batch_op:
        batch_op.alter_column(
            'id', existing_type=sa.String(length=255), type_=sa.String(length=64), existing_nullable=False,
        )
