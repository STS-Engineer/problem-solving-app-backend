"""create table for storing conversations messages

Revision ID: 24fcecc639c7
Revises: e5e14c42fcef
Create Date: 2026-02-24 12:04:03.525230

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '24fcecc639c7'
down_revision: Union[str, Sequence[str], None] = 'e5e14c42fcef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. step_conversations ─────────────────────────────────────────────────
    op.create_table(
        'step_conversations',
        sa.Column('id',             sa.Integer(),     primary_key=True, autoincrement=True),
        sa.Column('report_step_id', sa.Integer(),     sa.ForeignKey('report_steps.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('section_key',    sa.String(64),    nullable=False,
                  comment='e.g. five_w_2h | deviation | is_is_not'),
        sa.Column('role',           sa.String(16),    nullable=False,
                  comment='assistant | user'),
        sa.Column('content',        sa.Text(),        nullable=False),
        sa.Column('message_index',  sa.Integer(),     nullable=False,
                  comment='0-based ordering within the section conversation'),
        sa.Column('meta',           postgresql.JSONB(), nullable=True,
                  comment='optional: extracted_fields, confidence, etc.'),
        sa.Column('created_at',     sa.DateTime(),    nullable=False,
                  server_default=sa.text("NOW()")),
    )

    op.create_index(
        'idx_step_conv_step_section',
        'step_conversations',
        ['report_step_id', 'section_key'],
    )
    op.create_index(
        'idx_step_conv_ordering',
        'step_conversations',
        ['report_step_id', 'section_key', 'message_index'],
    )

    # ── 2. Add extracted_fields JSONB to step_validation ─────────────────────
    # Stores fields extracted by the chatbot before formal AI validation
    op.add_column(
        'step_validation',
        sa.Column(
            'extracted_fields',
            postgresql.JSONB(),
            nullable=True,
            comment='Fields extracted from conversation before formal validation',
        ),
    )


def downgrade() -> None:
    op.drop_column('step_validation', 'extracted_fields')
    op.drop_index('idx_step_conv_ordering', table_name='step_conversations')
    op.drop_index('idx_step_conv_step_section', table_name='step_conversations')
    op.drop_table('step_conversations')