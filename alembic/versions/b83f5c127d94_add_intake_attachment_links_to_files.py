"""add intake/complaint links + source to files (email attachments)

Revision ID: b83f5c127d94
Revises: a7d1e9c4b820
Create Date: 2026-07-15 01:00:00.000000

Lets email-intake attachments live in the `files` table directly (no StepFile):
  - uploaded_by becomes nullable (email files have no user)
  - source            : origin tag, e.g. 'email_intake'
  - intake_id         : FK email_intake (set at ingestion)
  - complaint_id      : FK complaints (set at promotion)
  - description       : agent-generated description of the file
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b83f5c127d94"
down_revision: Union[str, Sequence[str], None] = "a7d1e9c4b820"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("files", "uploaded_by", existing_type=sa.Integer(), nullable=True)

    op.add_column("files", sa.Column("source", sa.String(length=30), nullable=True))
    op.add_column("files", sa.Column("intake_id", sa.Integer(), nullable=True))
    op.add_column("files", sa.Column("complaint_id", sa.Integer(), nullable=True))
    op.add_column(
        "files", sa.Column("description", sa.String(length=1000), nullable=True)
    )

    op.create_index(op.f("ix_files_source"), "files", ["source"], unique=False)
    op.create_index(op.f("ix_files_intake_id"), "files", ["intake_id"], unique=False)
    op.create_index(
        op.f("ix_files_complaint_id"), "files", ["complaint_id"], unique=False
    )
    op.create_foreign_key(
        "fk_files_intake_id",
        "files",
        "email_intake",
        ["intake_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_files_complaint_id",
        "files",
        "complaints",
        ["complaint_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_files_complaint_id", "files", type_="foreignkey")
    op.drop_constraint("fk_files_intake_id", "files", type_="foreignkey")
    op.drop_index(op.f("ix_files_complaint_id"), table_name="files")
    op.drop_index(op.f("ix_files_intake_id"), table_name="files")
    op.drop_index(op.f("ix_files_source"), table_name="files")

    op.drop_column("files", "description")
    op.drop_column("files", "complaint_id")
    op.drop_column("files", "intake_id")
    op.drop_column("files", "source")

    op.alter_column("files", "uploaded_by", existing_type=sa.Integer(), nullable=False)
