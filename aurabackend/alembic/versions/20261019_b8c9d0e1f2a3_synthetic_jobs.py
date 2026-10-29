"""gateway_synthetic_jobs -- synthetic-data jobs stored in the database

BUG-430: the synthetic router kept its jobs in one worker's memory, so with several
gateway workers a poll for a job often reached a worker that had never seen it (404),
and every restart lost every job. One row per job; the API's record is a JSON column.

Additive: a new table and its index, nothing existing changes; the downgrade drops it.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-19 00:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # BUG-554: api_gateway/persistence.py's create_all runs on every gateway start
    # (and as the fallback of a worker whose migration failed), so this table can
    # already exist. An unguarded CREATE aborts the whole upgrade and leaves the
    # database stuck at the previous revision for good.
    if 'gateway_synthetic_jobs' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'gateway_synthetic_jobs',
        sa.Column('job_id', sa.String(length=32), nullable=False),
        sa.Column('workspace_id', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('record_json', sa.Text(), nullable=False),
        sa.Column('created_ts', sa.Float(), nullable=False),
        sa.Column('updated_ts', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('job_id'),
    )
    op.create_index(
        op.f('ix_gateway_synthetic_jobs_workspace_id'), 'gateway_synthetic_jobs', ['workspace_id'], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_gateway_synthetic_jobs_workspace_id'), table_name='gateway_synthetic_jobs')
    op.drop_table('gateway_synthetic_jobs')
