"""${message}"""
from alembic import op
import sqlalchemy as sa

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = None
depends_on = None

def upgrade():
    ${upgrades if upgrades else "pass"}

def downgrade():
    raise RuntimeError("Restore the verified matching database/audio backup or apply a reviewed forward repair")
