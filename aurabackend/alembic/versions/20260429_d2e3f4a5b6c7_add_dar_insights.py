"""add dar_insights table

Revision ID: d2e3f4a5b6c7
Revises: c7d8e9f0a1b2
Create Date: 2026-04-29 12:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'dar_insights',
        sa.Column('id', sa.String(length=64), primary_key=True),
        sa.Column('source_id', sa.String(length=128), nullable=False),
        sa.Column('table_name', sa.String(length=255), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('sql_query', sa.Text(), nullable=True),
        sa.Column('finding_type', sa.String(length=32), nullable=False, server_default='summary'),
        sa.Column('summary', sa.Text(), nullable=False, server_default=''),
        sa.Column('score', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('is_anomaly', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('payload', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('run_id', sa.String(length=64), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_dar_insights_source_table', 'dar_insights', ['source_id', 'table_name'])
    op.create_index('ix_dar_insights_score', 'dar_insights', ['score'])
    op.create_index('ix_dar_insights_finding_type', 'dar_insights', ['finding_type'])


def downgrade() -> None:
    op.drop_index('ix_dar_insights_finding_type', table_name='dar_insights')
    op.drop_index('ix_dar_insights_score', table_name='dar_insights')
    op.drop_index('ix_dar_insights_source_table', table_name='dar_insights')
    op.drop_table('dar_insights')
