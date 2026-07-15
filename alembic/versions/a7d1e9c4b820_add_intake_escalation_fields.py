"""add pre-complaint escalation fields to email_intake

Revision ID: a7d1e9c4b820
Revises: f4c2a9d7e1b3
Create Date: 2026-07-15 00:30:00.000000

Adds tracking columns so intakes that have not yet become complaints can be
escalated (chased) while they wait:
  - awaiting_cqt        : received, no CQT assigned
  - awaiting_complaint  : CQT assigned, complaint not created yet
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a7d1e9c4b820"
down_revision: Union[str, Sequence[str], None] = "f4c2a9d7e1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "email_intake",
        sa.Column("escalation_stage", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "email_intake",
        sa.Column(
            "escalation_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "email_intake",
        sa.Column("escalation_sent_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "email_intake",
        sa.Column(
            "escalation_log",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("email_intake", "escalation_log")
    op.drop_column("email_intake", "escalation_sent_at")
    op.drop_column("email_intake", "escalation_count")
    op.drop_column("email_intake", "escalation_stage")
