"""make complaints.reported_by and reports.created_by nullable

Revision ID: c1a7d9e4f2b0
Revises: 85f2f8bc726d
Create Date: 2026-07-14 00:00:00.000000

Allows complaints (and their reports) to be created without an internal
author user — required for email/bot-sourced complaints. The customer
identity for such complaints is captured via complaints.cqt_email.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c1a7d9e4f2b0"
down_revision: Union[str, Sequence[str], None] = "85f2f8bc726d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "complaints",
        "reported_by",
        existing_type=sa.Integer(),
        nullable=True,
        comment="Internal user who created the complaint; NULL for email/bot-sourced complaints",
        existing_comment="User who created complaint",
    )
    op.alter_column(
        "reports",
        "created_by",
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade() -> None:
    """Downgrade schema.

    NOTE: this will fail if any rows have NULL reported_by / created_by.
    Backfill those to a valid user id before downgrading.
    """
    op.alter_column(
        "reports",
        "created_by",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "complaints",
        "reported_by",
        existing_type=sa.Integer(),
        nullable=False,
        comment="User who created complaint",
        existing_comment="Internal user who created the complaint; NULL for email/bot-sourced complaints",
    )
