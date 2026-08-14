"""add graph_subscription table

Revision ID: a1b2c3d4e5f6
Revises: c1a6f8d92e07
Create Date: 2026-08-14 00:00:00.000000

Tracks the Microsoft Graph change-notification subscription that watches the
shared claims mailbox for new mail (internal intake agent — replaces the
external MCP agent's polling with a webhook push).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "c1a6f8d92e07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "graph_subscription",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("subscription_id", sa.String(length=100), nullable=False),
        sa.Column("resource", sa.String(length=500), nullable=False),
        sa.Column("change_type", sa.String(length=100), nullable=False),
        sa.Column("notification_url", sa.String(length=500), nullable=False),
        sa.Column("client_state", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="active"),
        sa.Column("last_renewed_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_graph_subscription_subscription_id",
        "graph_subscription",
        ["subscription_id"],
        unique=True,
    )
    op.create_index(
        "ix_graph_subscription_expires_at",
        "graph_subscription",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_graph_subscription_expires_at", table_name="graph_subscription")
    op.drop_index("ix_graph_subscription_subscription_id", table_name="graph_subscription")
    op.drop_table("graph_subscription")
