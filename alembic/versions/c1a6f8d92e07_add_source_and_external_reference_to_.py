"""add source and external_reference to complaints (Monday archive import)

Revision ID: c1a6f8d92e07
Revises: b83f5c127d94
Create Date: 2026-07-31 00:00:00.000000

Lets historical, already-closed complaints be imported from the Monday.com
"Close" board export without a Report/ReportStep 8D trail:
  - source              : origin tag, e.g. 'monday_import' (NULL = created via
                          the normal form/intake flow)
  - external_reference  : the source system's own id (Monday item_id), used
                          to make the import idempotent — re-running it must
                          not create duplicate complaints
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c1a6f8d92e07"
down_revision: Union[str, Sequence[str], None] = "b83f5c127d94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "complaints", sa.Column("source", sa.String(length=30), nullable=True)
    )
    op.add_column(
        "complaints",
        sa.Column("external_reference", sa.String(length=100), nullable=True),
    )

    op.create_index(
        op.f("ix_complaints_source"), "complaints", ["source"], unique=False
    )
    # Not globally unique on its own (two different source systems could reuse
    # an id) — uniqueness is enforced per-source in the import script itself.
    op.create_index(
        op.f("ix_complaints_external_reference"),
        "complaints",
        ["external_reference"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_complaints_external_reference"), table_name="complaints")
    op.drop_index(op.f("ix_complaints_source"), table_name="complaints")

    op.drop_column("complaints", "external_reference")
    op.drop_column("complaints", "source")
