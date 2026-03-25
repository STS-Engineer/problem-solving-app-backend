"""refactor complaint table, add columns to report step, create logger table

Revision ID: a9f00d5c00a7
Revises: a789634a441e
Create Date: 2026-02-27 09:16:41.466028

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a9f00d5c00a7"
down_revision: Union[str, Sequence[str], None] = "a789634a441e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


"""
Alembic migration: refactor complaint table + add complaint_audit_log

Revision ID: a4f2e9c1d8b7
Revises: <previous_revision_id>   ← replace with your actual last revision
Create Date: 2024-01-20

Changes:
  complaints table:
    REMOVE : severity, resolved_at, assigned_to (FK), quality_manager (FK)
    ADD    : cqt_email, quality_manager_email, plant_manager_email, approved_by_email
    RENAME : (none — new columns are semantically cleaner)

  report_steps table:
    ADD    : due_date, is_overdue, escalation_count, escalation_sent_at

  NEW    : complaint_audit_log table

ROLLBACK WARNING:
  Dropping severity and resolved_at is destructive.
  Run a data backup before applying to production.
"""


def upgrade() -> None:

    # ── 1. Refactor complaints table ─────────────────────────────────────────

    # Remove old FK columns (drop FK constraints first)
    op.drop_constraint("complaints_assigned_to_fkey", "complaints", type_="foreignkey")
    op.drop_constraint(
        "complaints_quality_manager_fkey", "complaints", type_="foreignkey"
    )

    op.drop_column("complaints", "severity")
    op.drop_column("complaints", "resolved_at")
    op.drop_column("complaints", "assigned_to")
    op.drop_column("complaints", "quality_manager")

    # Add new email columns
    op.add_column(
        "complaints",
        sa.Column(
            "cqt_email",
            sa.String(255),
            nullable=True,
            comment="Customer Quality Technician/Engineer email",
        ),
    )
    op.add_column(
        "complaints",
        sa.Column(
            "quality_manager_email",
            sa.String(255),
            nullable=True,
            comment="AVOCarbon quality manager email",
        ),
    )
    op.add_column(
        "complaints",
        sa.Column(
            "plant_manager_email",
            sa.String(255),
            nullable=True,
            comment="Plant manager email — used for L3/L4 escalation",
        ),
    )
    op.add_column(
        "complaints",
        sa.Column(
            "approved_by_email",
            sa.String(255),
            nullable=True,
            comment="Email of person who approved closure",
        ),
    )

    # Add indexes on new email columns
    op.create_index("ix_complaints_cqt_email", "complaints", ["cqt_email"])
    op.create_index(
        "ix_complaints_quality_manager_email", "complaints", ["quality_manager_email"]
    )
    op.create_index(
        "ix_complaints_plant_manager_email", "complaints", ["plant_manager_email"]
    )

    # ── 2. Extend report_steps table ─────────────────────────────────────────

    op.add_column(
        "report_steps",
        sa.Column(
            "due_date",
            sa.DateTime,
            nullable=True,
            comment="SLA-based deadline for this step",
        ),
    )
    op.add_column(
        "report_steps",
        sa.Column(
            "is_overdue",
            sa.Boolean,
            nullable=False,
            server_default="false",
            comment="True once the due_date has passed without completion",
        ),
    )
    op.add_column(
        "report_steps",
        sa.Column(
            "escalation_count",
            sa.Integer,
            nullable=False,
            server_default="0",
            comment="Number of escalation emails sent (0–4)",
        ),
    )
    op.add_column(
        "report_steps",
        sa.Column(
            "escalation_sent_at",
            sa.DateTime,
            nullable=True,
            comment="Timestamp of the most recent escalation email",
        ),
    )
    op.add_column(
        "report_steps",
        sa.Column(
            "cost",
            sa.Numeric(12, 2),
            nullable=True,
            comment="Cost attributed to this step",
        ),
    )

    op.create_index("ix_report_steps_due_date", "report_steps", ["due_date"])
    op.create_index("ix_report_steps_is_overdue", "report_steps", ["is_overdue"])
    op.create_index("ix_report_steps_esc_count", "report_steps", ["escalation_count"])

    # ── 3. Create complaint_audit_log table ───────────────────────────────────

    op.create_table(
        "complaint_audit_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        # References
        sa.Column(
            "complaint_id",
            sa.Integer,
            sa.ForeignKey("complaints.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "report_id",
            sa.Integer,
            sa.ForeignKey("reports.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "step_id",
            sa.Integer,
            sa.ForeignKey("report_steps.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        # Classification
        sa.Column("step_code", sa.String(10), nullable=True, index=True),
        sa.Column("event_type", sa.String(50), nullable=False, index=True),
        # Actor
        sa.Column("performed_by_email", sa.String(255), nullable=True, index=True),
        # Payload
        sa.Column(
            "event_data",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        # Timestamp
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            index=True,
            server_default=sa.text("NOW()"),
        ),
    )

    # Composite indexes
    op.create_index(
        "idx_audit_complaint_step", "complaint_audit_log", ["complaint_id", "step_code"]
    )
    op.create_index(
        "idx_audit_complaint_type",
        "complaint_audit_log",
        ["complaint_id", "event_type"],
    )
    op.create_index(
        "idx_audit_data_gin",
        "complaint_audit_log",
        ["event_data"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    # ── Reverse order ──────────────────────────────────────────────────────────

    # Drop complaint_audit_log
    op.drop_index("idx_audit_data_gin", table_name="complaint_audit_log")
    op.drop_index("idx_audit_complaint_type", table_name="complaint_audit_log")
    op.drop_index("idx_audit_complaint_step", table_name="complaint_audit_log")
    op.drop_table("complaint_audit_log")

    # Reverse report_steps additions
    op.drop_index("ix_report_steps_esc_count", table_name="report_steps")
    op.drop_index("ix_report_steps_is_overdue", table_name="report_steps")
    op.drop_index("ix_report_steps_due_date", table_name="report_steps")
    op.drop_column("report_steps", "escalation_sent_at")
    op.drop_column("report_steps", "escalation_count")
    op.drop_column("report_steps", "is_overdue")
    op.drop_column("report_steps", "due_date")

    # Reverse complaints changes (add back old columns)
    op.drop_index("ix_complaints_plant_manager_email", table_name="complaints")
    op.drop_index("ix_complaints_quality_manager_email", table_name="complaints")
    op.drop_index("ix_complaints_cqt_email", table_name="complaints")
    op.drop_column("complaints", "approved_by_email")
    op.drop_column("complaints", "plant_manager_email")
    op.drop_column("complaints", "quality_manager_email")
    op.drop_column("complaints", "cqt_email")

    # Re-add old columns (data will be lost)
    op.add_column("complaints", sa.Column("severity", sa.String(20), nullable=True))
    op.add_column("complaints", sa.Column("resolved_at", sa.DateTime, nullable=True))
    op.add_column("complaints", sa.Column("assigned_to", sa.Integer, nullable=True))
    op.add_column("complaints", sa.Column("quality_manager", sa.Integer, nullable=True))
    # Note: FK constraints not restored in downgrade for simplicity
