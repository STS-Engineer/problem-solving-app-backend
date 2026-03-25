"""register avomembers table

Revision ID: 37c966f030a0
Revises: 1395afc7f12e
Create Date: 2026-03-06 10:24:41.335448

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "37c966f030a0"
down_revision: Union[str, Sequence[str], None] = "1395afc7f12e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
