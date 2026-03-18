"""add webhook_jobs table

Revision ID: 8a29a0926fdd
Revises: 63fbace0634c
Create Date: 2026-03-13 09:54:34.326139

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8a29a0926fdd'
down_revision: Union[str, Sequence[str], None] = '63fbace0634c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    # Create ENUM safely
    op.execute("""
    DO $$ BEGIN
        CREATE TYPE webhook_status_enum AS ENUM (
            'pending',
            'locked',
            'done',
            'failed',
            'abandoned'
        );
    EXCEPTION
        WHEN duplicate_object THEN NULL;
    END $$;
    """)

    op.create_table(
        "webhook_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("complaint_id", sa.Integer(), nullable=False),
        sa.Column("complaint_ref", sa.String(50), nullable=False),
        sa.Column("complaint_type", sa.String(50), nullable=False),
        sa.Column(
            "event",
            sa.String(100),
            nullable=False,
            server_default="complaint.created",
        ),
        sa.Column("target_url", sa.String(2048), nullable=False),

        # 👇 IMPORTANT : utiliser le type PostgreSQL existant
        sa.Column(
            "status",
            sa.dialects.postgresql.ENUM(
                name="webhook_status_enum",
                create_type=False
            ),
            nullable=False,
            server_default="pending"
        ),

        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("retry_after", sa.DateTime(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("last_http_status", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_index(
        "ix_webhook_jobs_worker",
        "webhook_jobs",
        ["status", "retry_after"],
    )

    op.create_index(
        "ix_webhook_jobs_complaint_id",
        "webhook_jobs",
        ["complaint_id"],
    )


def downgrade() -> None:

    op.drop_index("ix_webhook_jobs_worker", table_name="webhook_jobs")
    op.drop_index("ix_webhook_jobs_complaint_id", table_name="webhook_jobs")
    op.drop_table("webhook_jobs")

    op.execute("DROP TYPE IF EXISTS webhook_status_enum")