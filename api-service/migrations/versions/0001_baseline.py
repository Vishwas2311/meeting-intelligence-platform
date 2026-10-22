"""Original reviewed schema. Existing deployments must verify before stamping."""

from alembic import op

from migrations.baseline import Base

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(op.get_bind(), checkfirst=False)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore the verified database/audio backup")
