"""generalize email_outbox for intake notifications

Revision ID: c4d5e6f7a8b9
Revises: b2c3d4e5f6a7
Create Date: 2026-09-18 12:00:00.000000

email_outbox previously only served 8D-step escalation emails (subject/body
rebuilt from step_id/complaint_id at retry time). Email-intake notifications
(new complaint -> QM/PM, CQE assignment, follow-up) were sent fire-and-forget
via _send_sync with no DB record and no retry on failure — a silent send
failure meant nobody was ever told a complaint needed action.

Adds a `kind` discriminator ('escalation' | 'intake'), an intake_id FK, and
stored subject/body_html so intake notifications can be retried generically
without needing to reload/rebuild complaint-shaped state. escalation_level
becomes nullable since it doesn't apply to intake notifications.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "email_outbox",
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="escalation"),
    )
    op.add_column(
        "email_outbox",
        sa.Column(
            "intake_id",
            sa.Integer(),
            sa.ForeignKey("email_intake.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("email_outbox", sa.Column("subject", sa.String(length=500), nullable=True))
    op.add_column("email_outbox", sa.Column("body_html", sa.Text(), nullable=True))
    op.create_index("ix_email_outbox_intake_id", "email_outbox", ["intake_id"])
    op.alter_column("email_outbox", "escalation_level", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.alter_column("email_outbox", "escalation_level", existing_type=sa.Integer(), nullable=False)
    op.drop_index("ix_email_outbox_intake_id", table_name="email_outbox")
    op.drop_column("email_outbox", "body_html")
    op.drop_column("email_outbox", "subject")
    op.drop_column("email_outbox", "intake_id")
    op.drop_column("email_outbox", "kind")
