"""remove step_validation table

Revision ID: a789634a441e
Revises: 24fcecc639c7
Create Date: 2026-02-26 09:40:56.407156

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a789634a441e"
down_revision: Union[str, Sequence[str], None] = "24fcecc639c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_table("step_validation")


def downgrade():
    op.create_table(
        "step_validation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_step_id", sa.Integer(), nullable=False, index=True),
        sa.Column("decision", sa.String(length=20), nullable=False, index=True),
        sa.Column("missing", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("issues", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("suggestions", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("professional_rewrite", sa.Text(), nullable=True),
        sa.Column("validated_at", sa.DateTime(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("section_key", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["report_step_id"], ["report_steps.id"], ondelete="CASCADE"
        ),
    )
