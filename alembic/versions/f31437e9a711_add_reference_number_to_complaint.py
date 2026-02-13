"""add reference number to complaint

Revision ID: f31437e9a711
Revises: 498b1ba7e249
Create Date: 2026-02-11 14:17:00.483672

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f31437e9a711'
down_revision: Union[str, Sequence[str], None] = '498b1ba7e249'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add reference_number column to complaints table."""
    # Add the column
    op.add_column('complaints', 
        sa.Column('reference_number', sa.String(length=50), nullable=True, unique=True)
    )
    
    # Create unique index
    op.create_index('ix_complaints_reference_number', 'complaints', ['reference_number'], unique=True)
    
    # Generate reference numbers for existing complaints
    # Format: CMP-YYYY-NNNN (e.g., CMP-2026-0001)
    op.execute("""
        UPDATE complaints
        SET reference_number = 'CMP-' || 
            TO_CHAR(created_at, 'YYYY') || '-' ||
            LPAD(id::text, 4, '0')
        WHERE reference_number IS NULL
    """)
    
    # Make column not nullable after populating
    op.alter_column('complaints', 'reference_number', nullable=False)


def downgrade() -> None:
    """Remove reference_number column from complaints table."""
    op.drop_index('ix_complaints_reference_number', table_name='complaints')
    op.drop_column('complaints', 'reference_number')
