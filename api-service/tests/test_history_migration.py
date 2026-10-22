from sqlalchemy import create_engine, select
from test_migrations import upgrade


def test_explicit_history_copy_preserves_source_and_requires_owner_mapping(client, tmp_path):
    import pytest

    from app.database import Base
    from migrations.baseline import Base as Legacy
    from scripts.migrate_history import checksum, migrate

    source_path = tmp_path / "source.db"
    source = create_engine(f"sqlite:///{source_path}")
    Legacy.metadata.create_all(source)
    with source.begin() as db:
        db.execute(
            Legacy.metadata.tables["meetings"]
            .insert()
            .values(id="m", title="Synthetic only", created_by="old-owner")
        )
    source.dispose()
    original = checksum(source_path)
    audio = tmp_path / "original-audio"
    audio.mkdir()
    with pytest.raises(RuntimeError, match="owner"):
        migrate(source_path, audio, {})
    inventory = migrate(source_path, audio, {"old-owner": "jwt:synthetic-owner"})
    assert inventory["rows"]["meetings"] == 1
    target = create_engine(f"sqlite:///{tmp_path / 'target.db'}")
    upgrade(target)
    result = migrate(
        source_path,
        audio,
        {"old-owner": "jwt:synthetic-owner"},
        tmp_path / "new-audio",
        str(target.url),
    )
    assert result["mode"] == "copied"
    assert checksum(source_path) == original
    with target.connect() as db:
        assert (
            db.scalar(select(Base.metadata.tables["meetings"].c.created_by))
            == "jwt:synthetic-owner"
        )
    target.dispose()
