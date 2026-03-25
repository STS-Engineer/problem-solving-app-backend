"""create plan push log model

Revision ID: 0e56bf99aec2
Revises: 8a29a0926fdd
Create Date: 2026-03-23 14:02:04.754794

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0e56bf99aec2"
down_revision: Union[str, Sequence[str], None] = "8a29a0926fdd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Fix cost column: Integer → Numeric(12,2) ───────────────────────────
    op.alter_column(
        "report_steps",
        "cost",
        existing_type=sa.Integer(),
        type_=sa.Numeric(precision=12, scale=2),
        existing_nullable=True,
        postgresql_using="cost::numeric(12,2)",
    )
    # add currency column if not yet present
    op.add_column(
        "report_steps",
        sa.Column(
            "cost_currency",
            sa.String(10),
            nullable=True,
            comment="ISO currency code for the step cost, e.g. EUR",
        ),
    )

    # ── 2. Create plan_push_log ───────────────────────────────────────────────
    op.create_table(
        "plan_push_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "report_id",
            sa.Integer(),
            sa.ForeignKey("reports.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "step_id",
            sa.Integer(),
            sa.ForeignKey("report_steps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("external_root_sujet_id", sa.Integer(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("report_id", name="uq_plan_push_log_report"),
    )
    op.create_index("ix_plan_push_log_status", "plan_push_log", ["status"])


def downgrade() -> None:
    op.drop_table("plan_push_log")
    op.drop_column("report_steps", "cost_currency")
    op.alter_column(
        "report_steps",
        "cost",
        existing_type=sa.Numeric(precision=12, scale=2),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using="cost::integer",
    )
