"""add CQT assignment columns to email_intake

Revision ID: e3c9f2a71b45
Revises: d2b8e1f5a3c4
Create Date: 2026-07-14 01:00:00.000000

Additive only: adds assigned_cqe_email / assigned_by / assigned_at so a QM can
assign an internal CQT (Customer Quality Engineer) to an email intake.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e3c9f2a71b45"
down_revision: Union[str, Sequence[str], None] = "d2b8e1f5a3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "email_intake",
        sa.Column("assigned_cqe_email", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "email_intake", sa.Column("assigned_by", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "email_intake", sa.Column("assigned_at", sa.DateTime(), nullable=True)
    )
    op.create_index(
        op.f("ix_email_intake_assigned_cqe_email"),
        "email_intake",
        ["assigned_cqe_email"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_email_intake_assigned_cqe_email"), table_name="email_intake"
    )
    op.drop_column("email_intake", "assigned_at")
    op.drop_column("email_intake", "assigned_by")
    op.drop_column("email_intake", "assigned_cqe_email")
