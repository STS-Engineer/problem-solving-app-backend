"""update plant_enum values

Revision ID: 85f2f8bc726d
Revises: 0e56bf99aec2
"""

from typing import Sequence, Union
from alembic import op

revision: str = "85f2f8bc726d"
down_revision: Union[str, Sequence[str], None] = "0e56bf99aec2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # 1. Create the new enum
    op.execute("""
        CREATE TYPE plant_enum_new AS ENUM (
            'MONTERREY',
            'KUNSHAN',
            'CHENNAI',
            'DAEGU',
            'TIANJIN',
            'POITIERS',
            'FRANKFURT',
            'SCEET',
            'SAME',
            'AMIENS',
            'ANHUI',
            'KOREA',
            'NADHOUR'
        )
    """)

    # 2. Detach ALL columns from old enum by casting to text
    op.execute("""
        ALTER TABLE complaints
        ALTER COLUMN avocarbon_plant TYPE text
        USING avocarbon_plant::text
    """)

    op.execute("""
        ALTER TABLE reports
        ALTER COLUMN plant TYPE text
        USING plant::text
    """)

    # 3. Fix existing data values
    op.execute("""
        UPDATE complaints
        SET avocarbon_plant = 'DAEGU'
        WHERE avocarbon_plant IN ('DAUGU', 'DAUEGU')
    """)

    op.execute("""
        UPDATE reports
        SET plant = 'DAEGU'
        WHERE plant IN ('DAUGU', 'DAUEGU')
    """)

    # 4. Drop old enum
    op.execute("DROP TYPE plant_enum")

    # 5. Rename new enum to official name
    op.execute("ALTER TYPE plant_enum_new RENAME TO plant_enum")

    # 6. Reattach columns to the renamed enum
    op.execute("""
        ALTER TABLE complaints
        ALTER COLUMN avocarbon_plant TYPE plant_enum
        USING avocarbon_plant::plant_enum
    """)

    op.execute("""
        ALTER TABLE reports
        ALTER COLUMN plant TYPE plant_enum
        USING plant::plant_enum
    """)


def downgrade():
    # 1. Recreate old enum
    op.execute("""
        CREATE TYPE plant_enum_old AS ENUM (
            'MONTERREY',
            'KUNSHAN',
            'CHENNAI',
            'DAUGU',
            'TIANJIN',
            'POITIERS',
            'FRANKFURT',
            'SCEET',
            'SAME',
            'AMIENS',
            'ANHUI',
            'KOREA'
        )
    """)

    # 2. Detach columns from current enum
    op.execute("""
        ALTER TABLE complaints
        ALTER COLUMN avocarbon_plant TYPE text
        USING avocarbon_plant::text
    """)

    op.execute("""
        ALTER TABLE reports
        ALTER COLUMN plant TYPE text
        USING plant::text
    """)

    # 3. Convert data back
    op.execute("""
        UPDATE complaints
        SET avocarbon_plant = 'DAUGU'
        WHERE avocarbon_plant = 'DAEGU'
    """)

    op.execute("""
        UPDATE reports
        SET plant = 'DAUGU'
        WHERE plant = 'DAEGU'
    """)

    # 4. Drop current enum
    op.execute("DROP TYPE plant_enum")

    # 5. Rename old enum back
    op.execute("ALTER TYPE plant_enum_old RENAME TO plant_enum")

    # 6. Reattach columns
    op.execute("""
        ALTER TABLE complaints
        ALTER COLUMN avocarbon_plant TYPE plant_enum
        USING avocarbon_plant::plant_enum
    """)

    op.execute("""
        ALTER TABLE reports
        ALTER COLUMN plant TYPE plant_enum
        USING plant::plant_enum
    """)