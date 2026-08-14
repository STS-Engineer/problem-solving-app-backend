"""add notification breadcrumb to graph_subscription

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-14 12:00:00.000000

Lets you confirm "did a Graph notification actually arrive?" by querying the
DB / GET /admin/graph/subscription-status, instead of tailing application
logs — the webhook now stamps these fields on every notification received.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "graph_subscription",
        sa.Column("last_notification_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "graph_subscription",
        sa.Column("last_notification_message_id", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "graph_subscription",
        sa.Column(
            "notification_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("graph_subscription", "notification_count")
    op.drop_column("graph_subscription", "last_notification_message_id")
    op.drop_column("graph_subscription", "last_notification_at")
