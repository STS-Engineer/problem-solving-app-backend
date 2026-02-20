"""add section_key

Revision ID: e5e14c42fcef
Revises: 3831822e043f
Create Date: 2026-02-19 14:33:55.349472

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5e14c42fcef'
down_revision: Union[str, Sequence[str], None] = '3831822e043f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    # 2. Add section_key column (nullable — NULL means "full step" e.g. D1)
    op.add_column('step_validation', sa.Column(
        'section_key', sa.String(64), nullable=True,
        comment="NULL=full step (D1), or section name e.g. 'five_w_2h'"
    ))

    # 3. New composite unique: one validation per (step + section)
    #    For D1 full-step rows section_key IS NULL, so we use a partial index
    op.create_unique_constraint(
        'uq_step_validation_step_section',
        'step_validation',
        ['report_step_id', 'section_key']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_step_validation_step_section', 'step_validation')
    op.drop_column('step_validation', 'section_key')
    op.create_unique_constraint(
        'step_validation_report_step_id_key',
        'step_validation',
        ['report_step_id']
    )
