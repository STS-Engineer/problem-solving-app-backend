"""add email_outbox table

Revision ID: 3a37c9264c56
Revises: 37c966f030a0
Create Date: 2026-03-06

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "3a37c9264c56"
down_revision = "37c966f030a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create table WITHOUT foreign key constraints first
    # (report_step and complaint already exist but Alembic doesn't track them
    #  in this migration chain — we add FKs separately with use_alter=True)
    op.create_table(
        "email_outbox",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("step_id", sa.Integer(), nullable=True),
        sa.Column("complaint_id", sa.Integer(), nullable=True),
        sa.Column("escalation_level", sa.Integer(), nullable=False),
        sa.Column(
            "recipients", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("cc", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="pending"
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "next_retry_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed', 'abandoned')",
            name="ck_email_outbox_status",
        ),
    )

    # Add FK constraints separately — use_alter avoids dependency resolution issues
    op.create_foreign_key(
        "fk_email_outbox_step_id",
        "email_outbox",
        "report_steps",
        ["step_id"],
        ["id"],
        ondelete="SET NULL",
        use_alter=True,
    )
    op.create_foreign_key(
        "fk_email_outbox_complaint_id",
        "email_outbox",
        "complaints",
        ["complaint_id"],
        ["id"],
        ondelete="SET NULL",
        use_alter=True,
    )

    # Indexes
    op.create_index(
        "ix_email_outbox_status_retry",
        "email_outbox",
        ["status", "next_retry_at"],
    )
    op.create_index("ix_email_outbox_step_id", "email_outbox", ["step_id"])
    op.create_index("ix_email_outbox_complaint_id", "email_outbox", ["complaint_id"])


def downgrade() -> None:
    op.drop_constraint("fk_email_outbox_step_id", "email_outbox", type_="foreignkey")
    op.drop_constraint(
        "fk_email_outbox_complaint_id", "email_outbox", type_="foreignkey"
    )
    op.drop_index("ix_email_outbox_status_retry", table_name="email_outbox")
    op.drop_index("ix_email_outbox_step_id", table_name="email_outbox")
    op.drop_index("ix_email_outbox_complaint_id", table_name="email_outbox")
    op.drop_table("email_outbox")
