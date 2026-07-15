"""quality managers per plant: quality_manager_email -> quality_manager_emails list

Revision ID: f4c2a9d7e1b3
Revises: e3c9f2a71b45
Create Date: 2026-07-15 00:00:00.000000

A plant can have several Quality Managers, and all of them must be notified of a
new complaint. This converts the single ``quality_manager_email`` column into a
JSON list ``quality_manager_emails`` on both ``plant_contacts`` and
``complaints``. Existing single values are preserved as one-element lists.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f4c2a9d7e1b3"
down_revision: Union[str, Sequence[str], None] = "e3c9f2a71b45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── plant_contacts: quality_manager_email → quality_manager_emails ───────
    op.add_column(
        "plant_contacts",
        sa.Column(
            "quality_manager_emails",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.execute(
        """
        UPDATE plant_contacts
        SET quality_manager_emails = to_jsonb(ARRAY[quality_manager_email])
        WHERE quality_manager_email IS NOT NULL
          AND btrim(quality_manager_email) <> ''
        """
    )
    op.drop_column("plant_contacts", "quality_manager_email")

    # ── complaints: quality_manager_email → quality_manager_emails ───────────
    # The old column was indexed; a JSON list column is not indexed the same way.
    op.execute("DROP INDEX IF EXISTS ix_complaints_quality_manager_email")
    op.add_column(
        "complaints",
        sa.Column(
            "quality_manager_emails",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE complaints
        SET quality_manager_emails = to_jsonb(ARRAY[quality_manager_email])
        WHERE quality_manager_email IS NOT NULL
          AND btrim(quality_manager_email) <> ''
        """
    )
    op.drop_column("complaints", "quality_manager_email")


def downgrade() -> None:
    # ── complaints ──────────────────────────────────────────────────────────
    op.add_column(
        "complaints",
        sa.Column("quality_manager_email", sa.String(length=255), nullable=True),
    )
    op.execute(
        """
        UPDATE complaints
        SET quality_manager_email = quality_manager_emails->>0
        WHERE quality_manager_emails IS NOT NULL
          AND jsonb_array_length(quality_manager_emails) > 0
        """
    )
    op.drop_column("complaints", "quality_manager_emails")
    op.create_index(
        op.f("ix_complaints_quality_manager_email"),
        "complaints",
        ["quality_manager_email"],
        unique=False,
    )

    # ── plant_contacts ──────────────────────────────────────────────────────
    op.add_column(
        "plant_contacts",
        sa.Column("quality_manager_email", sa.String(length=255), nullable=True),
    )
    op.execute(
        """
        UPDATE plant_contacts
        SET quality_manager_email = quality_manager_emails->>0
        WHERE quality_manager_emails IS NOT NULL
          AND jsonb_array_length(quality_manager_emails) > 0
        """
    )
    op.drop_column("plant_contacts", "quality_manager_emails")
