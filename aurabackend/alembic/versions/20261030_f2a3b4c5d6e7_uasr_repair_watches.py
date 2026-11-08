"""uasr_repair_watches: the post-heal canary of each proven repair

ADR-002 phase 2a item 3 (BUG-565), safety net steps 2 and 3. One row per proven
repair (rename, retype, rescale) a source gets: watched over its next N batches,
then kept (status 'deployed') or rolled back ('rolled_back'). The rollback is
written here before it takes effect, so a restart never brings it back.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-10-30 00:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # BUG-554: a startup create_all can already have built this from the current model.
    if 'uasr_repair_watches' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'uasr_repair_watches',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('source_id', sa.String(length=128), nullable=False),
        sa.Column('recovery_id', sa.String(length=64), nullable=False),
        sa.Column('repair_type', sa.String(length=16), nullable=False),
        sa.Column('column_name', sa.String(length=255), nullable=False),
        sa.Column('param', sa.Text(), nullable=False),
        sa.Column('proof', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('batches_seen', sa.Integer(), nullable=False),
        sa.Column('batches_required', sa.Integer(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_uasr_repair_watches_source_id', 'uasr_repair_watches', ['source_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_uasr_repair_watches_source_id', table_name='uasr_repair_watches')
    op.drop_table('uasr_repair_watches')
