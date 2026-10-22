"""Add production integrity, admission, local encryption and recovery metadata."""

import sqlalchemy as sa
from alembic import op

from migrations.baseline import Base as Original
from migrations.production_schema import Base

revision = "0002_production"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    for table in Base.metadata.sorted_tables:
        if table.name not in Original.metadata.tables:
            table.create(bind, checkfirst=False)
            continue
        previous = Original.metadata.tables[table.name]
        present = {c["name"] for c in sa.inspect(bind).get_columns(table.name)}
        for column in table.c:
            if column.name not in present:
                copy = sa.Column(
                    column.name, column.type, nullable=True, server_default=column.server_default
                )
                op.add_column(table.name, copy)
        existing_indexes = {i["name"] for i in sa.inspect(bind).get_indexes(table.name)}
        for index in table.indexes:
            if index.name not in existing_indexes:
                index.create(bind)
        for column in table.c:
            if column.name not in previous.c and column.unique:
                op.create_index(
                    f"uq_{table.name}_{column.name}", table.name, [column.name], unique=True
                )
    # Only values derivable from existing records are backfilled. Owner identity
    # translation from local-user to JWT subjects is an explicit separate mapping.
    op.execute(
        sa.text(
            "UPDATE jobs SET owner_actor=(SELECT created_by FROM meetings WHERE meetings.id=jobs.meeting_id) WHERE owner_actor IS NULL"
        )
    )
    if bind.dialect.name == "oracle":
        op.alter_column("worker_state", "id", type_=sa.String(128), existing_type=sa.String(20))
    for table, column, default in [
        ("meetings", "evidence_revision", "0"),
        ("moms", "revision", "0"),
        ("jobs", "completed_sections", "0"),
        ("audio_chunks", "storage_state", "'committed'"),
        ("jobs", "retry_attempts", "0"),
        ("jobs", "max_attempts", "3"),
        ("worker_state", "state", "'running'"),
    ]:
        op.execute(sa.text(f"UPDATE {table} SET {column}={default} WHERE {column} IS NULL"))

    # Capture a stable approved snapshot for legacy records that predate approval IDs.
    from sqlalchemy.orm import Session

    from migrations.production_schema import Job, Mom, MomRevision

    with Session(bind=bind) as db:
        for mom in db.scalars(
            sa.select(Mom).where(Mom.status == "approved", Mom.approved_revision_id.is_(None))
        ):
            snapshot = MomRevision(
                meeting_id=mom.meeting_id,
                data=mom.data,
                status="approved",
                actor=mom.approved_by or "system",
                revision_number=mom.revision,
                provenance={"migration": revision},
            )
            db.add(snapshot)
            db.flush()
            mom.approved_revision_id, mom.approved_at = snapshot.id, mom.updated_at
        for job in db.scalars(sa.select(Job).execution_options(yield_per=100)):
            checkpoint = job.checkpoint or {}
            job.completed_sections = len(checkpoint.get("sections", {}))
            job.retry_attempts = checkpoint.get("retry_count", 0)
            job.max_attempts = checkpoint.get("max_attempts", 3)
        db.flush()


def downgrade():
    raise RuntimeError("Use a reviewed forward repair or restore the matched Oracle/audio backup")
