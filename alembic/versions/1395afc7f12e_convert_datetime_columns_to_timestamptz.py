"""convert datetime columns to timestamptz

Revision ID: 1395afc7f12e
Revises: a9f00d5c00a7
Create Date: 2026-03-02 11:46:10.247238

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "1395afc7f12e"
down_revision: Union[str, Sequence[str], None] = "a9f00d5c00a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
