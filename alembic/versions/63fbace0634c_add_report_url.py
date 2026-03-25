"""add report url

Revision ID: 63fbace0634c
Revises: b419c9e1a8c4
Create Date: 2026-03-12 14:47:01.726952

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "63fbace0634c"
down_revision: Union[str, Sequence[str], None] = "b419c9e1a8c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("report_url", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("reports", "report_url")
