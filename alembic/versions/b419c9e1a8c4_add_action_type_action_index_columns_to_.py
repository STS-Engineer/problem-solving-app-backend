"""add action_type,action_index columns to step_files

Revision ID: b419c9e1a8c4
Revises: 3a37c9264c56
Create Date: 2026-03-10 08:13:33.909450

Adds two nullable columns to step_files so that files uploaded from D6
ActionCards can be scoped to a specific corrective action:

  action_type  VARCHAR(20)  — 'occurrence' | 'detection' | NULL
  action_index INTEGER      — 0-based position in the action array | NULL

Files without these set (NULL) are "step-level" files — existing behaviour
is fully preserved. No existing rows are affected.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b419c9e1a8c4'
down_revision: Union[str, Sequence[str], None] = '3a37c9264c56'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'step_files',
        sa.Column(
            'action_type',
            sa.String(20),
            nullable=True,
            comment="'occurrence' | 'detection' | NULL for step-level files",
        ),
    )
    op.add_column(
        'step_files',
        sa.Column(
            'action_index',
            sa.Integer(),
            nullable=True,
            comment='0-based index into the corrective action array | NULL for step-level files',
        ),
    )
    # Optional: index for fast per-action queries
    op.create_index(
        'idx_step_files_action',
        'step_files',
        ['report_step_id', 'action_type', 'action_index'],
        postgresql_where=sa.text('action_type IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('idx_step_files_action', table_name='step_files')
    op.drop_column('step_files', 'action_index')
    op.drop_column('step_files', 'action_type')
