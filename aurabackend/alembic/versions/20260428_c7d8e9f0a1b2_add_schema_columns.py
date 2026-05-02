"""add schema_columns table for structured MCP search

Revision ID: c7d8e9f0a1b2
Revises: a1b2c3d4e5f6
Create Date: 2026-04-28 12:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'schema_columns',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('source_id', sa.String(length=128), nullable=False),
        sa.Column('table_name', sa.String(length=255), nullable=False),
        sa.Column('column_name', sa.String(length=255), nullable=False),
        sa.Column('column_name_lower', sa.String(length=255), nullable=False),
        sa.Column('data_type', sa.String(length=64), nullable=False),
        sa.Column('is_nullable', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('ordinal_position', sa.Integer(), nullable=True),
        sa.Column('sample_values', sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('source_id', 'table_name', 'column_name', name='uq_schema_column'),
    )
    op.create_index('ix_schema_columns_source_id', 'schema_columns', ['source_id'])
    op.create_index('ix_schema_column_lower_name', 'schema_columns', ['column_name_lower'])
    op.create_index('ix_schema_column_table', 'schema_columns', ['source_id', 'table_name'])


def downgrade() -> None:
    op.drop_index('ix_schema_column_table', table_name='schema_columns')
    op.drop_index('ix_schema_column_lower_name', table_name='schema_columns')
    op.drop_index('ix_schema_columns_source_id', table_name='schema_columns')
    op.drop_table('schema_columns')
