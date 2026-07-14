"""add email_intake and plant_contacts tables

Revision ID: d2b8e1f5a3c4
Revises: c1a7d9e4f2b0
Create Date: 2026-07-14 00:30:00.000000

Adds the lenient email-intake staging table and the per-plant notification
contacts table, then seeds one (empty) contacts row per plant so routing has
a target. Until real emails are filled in, notifications fall back to
INTAKE_FALLBACK_EMAIL.
"""

from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d2b8e1f5a3c4"
down_revision: Union[str, Sequence[str], None] = "c1a7d9e4f2b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PLANTS = [
    "MONTERREY",
    "KUNSHAN",
    "CHENNAI",
    "DAEGU",
    "TIANJIN",
    "POITIERS",
    "FRANKFURT",
    "SCEET",
    "SAME",
    "AMIENS",
    "ANHUI",
    "KOREA",
    "NADHOUR",
]

# The plant_enum type already exists in the DB — reference it, never recreate.
plant_enum = postgresql.ENUM(name="plant_enum", create_type=False)


def upgrade() -> None:
    # ── plant_contacts ──────────────────────────────────────────────────────
    op.create_table(
        "plant_contacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plant", plant_enum, nullable=False),
        sa.Column(
            "cqe_emails",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("quality_manager_email", sa.String(length=255), nullable=True),
        sa.Column("plant_manager_email", sa.String(length=255), nullable=True),
        sa.Column("general_manager_email", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plant"),
    )
    op.create_index(
        op.f("ix_plant_contacts_plant"), "plant_contacts", ["plant"], unique=True
    )

    # ── email_intake ────────────────────────────────────────────────────────
    op.create_table(
        "email_intake",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_message_id", sa.String(length=998), nullable=False),
        sa.Column("conversation_id", sa.String(length=512), nullable=True),
        sa.Column("sender_email", sa.String(length=255), nullable=True),
        sa.Column("sender_name", sa.String(length=255), nullable=True),
        sa.Column("subject", sa.String(length=998), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.Column("raw_body", sa.Text(), nullable=True),
        sa.Column("raw_html", sa.Text(), nullable=True),
        sa.Column(
            "attachments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "extracted_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("ai_notes", sa.Text(), nullable=True),
        sa.Column(
            "missing_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("detected_plant", plant_enum, nullable=True),
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="pending_review",
        ),
        sa.Column(
            "notified_to",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("complaint_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["complaint_id"], ["complaints.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_message_id"),
    )
    op.create_index(
        op.f("ix_email_intake_source_message_id"),
        "email_intake",
        ["source_message_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_email_intake_conversation_id"),
        "email_intake",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_intake_sender_email"),
        "email_intake",
        ["sender_email"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_intake_detected_plant"),
        "email_intake",
        ["detected_plant"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_intake_status"), "email_intake", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_email_intake_complaint_id"),
        "email_intake",
        ["complaint_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_intake_created_at"),
        "email_intake",
        ["created_at"],
        unique=False,
    )

    # ── Seed one empty contacts row per plant ───────────────────────────────
    contacts = sa.table(
        "plant_contacts",
        sa.column("plant", plant_enum),
        sa.column("cqe_emails", postgresql.JSONB),
        sa.column("updated_at", sa.DateTime()),
    )
    now = datetime.now(timezone.utc)
    op.bulk_insert(
        contacts,
        [{"plant": p, "cqe_emails": [], "updated_at": now} for p in PLANTS],
    )


def downgrade() -> None:
    op.drop_table("email_intake")
    op.drop_index(op.f("ix_plant_contacts_plant"), table_name="plant_contacts")
    op.drop_table("plant_contacts")
