"""saved-query freshness state: last_success_at, schedule_set_at, late_slot_at

ADR-002 phase 2a item 4. The gateway's in-process saved-query scheduler is the only
scheduler free tier runs, and nothing noticed when a scheduled run stopped succeeding.
These columns let a periodic check mark a missed slot late, alert, and re-run it once:

- last_success_at: the last successful scheduled run (last_run_at moves on failures).
- schedule_set_at: when the schedule was last set, so re-enabling restarts the clock.
- late_slot_at: the last slot already alerted and re-run, so a restart or a second
  worker never re-runs the same slot.

Nullable and not backfilled: an existing row starts its clock on the first check.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-10-29 00:00:00.000000+00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'd0e1f2a3b4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = ('last_success_at', 'schedule_set_at', 'late_slot_at')


def upgrade() -> None:
    # BUG-554: a startup create_all can already have built these from the current model.
    existing = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('gateway_saved_queries')}
    for name in _COLUMNS:
        if name not in existing:
            op.add_column('gateway_saved_queries', sa.Column(name, sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('gateway_saved_queries', schema=None) as batch_op:
        for name in _COLUMNS:
            batch_op.drop_column(name)
