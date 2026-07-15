"""add source_id + HITL decision columns to uasr_recovery_records

The ORM (``uasr.models.RecoveryRecord``) grew five columns that no migration
ever created, so a fresh ``alembic upgrade head`` built
``uasr_recovery_records`` without them and any write touching a recovery
source id or a human-in-the-loop decision failed:

  - source_id          : denormalised source key (indexed) for per-source queries
  - generation_method  : how the shim was produced ('template' default)
  - decided_by         : operator who approved/rejected the recovery (HITL)
  - decision_note      : free-text rationale for the decision
  - decided_at         : timestamp of the decision

All nullable (or defaulted) so existing rows need no backfill.

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-05-01 07:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f4a5b6c7d8e9'
down_revision: Union[str, None] = 'e3f4a5b6c7d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('uasr_recovery_records', sa.Column('source_id', sa.String(length=128), nullable=True))
    op.add_column('uasr_recovery_records',
                  sa.Column('generation_method', sa.String(length=16), server_default='template', nullable=False))
    op.add_column('uasr_recovery_records', sa.Column('decided_by', sa.String(length=128), nullable=True))
    op.add_column('uasr_recovery_records', sa.Column('decision_note', sa.Text(), nullable=True))
    op.add_column('uasr_recovery_records', sa.Column('decided_at', sa.DateTime(), nullable=True))
    with op.batch_alter_table('uasr_recovery_records', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_uasr_recovery_records_source_id'), ['source_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('uasr_recovery_records', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_uasr_recovery_records_source_id'))
    op.drop_column('uasr_recovery_records', 'decided_at')
    op.drop_column('uasr_recovery_records', 'decision_note')
    op.drop_column('uasr_recovery_records', 'decided_by')
    op.drop_column('uasr_recovery_records', 'generation_method')
    op.drop_column('uasr_recovery_records', 'source_id')
