"""Verify an unversioned legacy schema before an explicit Alembic baseline stamp."""

import argparse

from alembic import command
from alembic.config import Config
from sqlalchemy import BigInteger, Boolean, Integer, Numeric, inspect


def verify(connection):
    from migrations.baseline import Base

    inspector = inspect(connection)
    if set(inspector.get_table_names()) != set(Base.metadata.tables):
        raise RuntimeError("Baseline table inventory mismatch; no stamp performed")
    for name, table in Base.metadata.tables.items():
        columns = {column["name"]: column for column in inspector.get_columns(name)}
        if set(columns) != set(table.c.keys()):
            raise RuntimeError(f"Baseline columns mismatch in {name}; no stamp performed")
        if set(inspector.get_pk_constraint(name)["constrained_columns"]) != set(
            table.primary_key.columns.keys()
        ):
            raise RuntimeError(f"Baseline primary key mismatch in {name}")
        for expected in table.c:
            actual = columns[expected.name]
            if actual["nullable"] != expected.nullable:
                raise RuntimeError(f"Baseline nullability mismatch in {name}.{expected.name}")
            if getattr(expected.type, "length", None) != getattr(actual["type"], "length", None):
                raise RuntimeError(f"Baseline length mismatch in {name}.{expected.name}")
            # Compare dialect affinities; Oracle CLOB is the intentional JSON representation.
            expected_type = expected.type.dialect_impl(connection.dialect)
            expected_affinity = (
                expected_type.impl._type_affinity
                if hasattr(expected_type, "impl")
                else expected_type._type_affinity
            )
            compatible = actual["type"]._type_affinity == expected_affinity
            if (
                connection.dialect.name == "oracle"
                and isinstance(expected.type, (Integer, Boolean))
                and isinstance(actual["type"], Numeric)
            ):
                precision = 19 if isinstance(expected.type, BigInteger) else 38
                compatible = actual["type"].scale == 0 and (
                    actual["type"].precision is None or actual["type"].precision >= precision
                )
            if not compatible:
                raise RuntimeError(f"Baseline column type mismatch in {name}.{expected.name}")
        expected_foreign = {
            (
                tuple(f.parent.name for f in constraint.elements),
                constraint.referred_table.name,
                tuple(f.column.name for f in constraint.elements),
            )
            for constraint in table.foreign_key_constraints
        }
        actual_foreign = {
            (tuple(f["constrained_columns"]), f["referred_table"], tuple(f["referred_columns"]))
            for f in inspector.get_foreign_keys(name)
        }
        if expected_foreign != actual_foreign:
            raise RuntimeError(f"Baseline foreign key mismatch in {name}")
        expected_unique = {
            tuple(c.columns.keys())
            for c in table.constraints
            if c.__class__.__name__ == "UniqueConstraint"
        }
        actual_unique = {tuple(c["column_names"]) for c in inspector.get_unique_constraints(name)}
        if not expected_unique <= actual_unique:
            raise RuntimeError(f"Baseline uniqueness mismatch in {name}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stamp",
        action="store_true",
        help="Stamp only after verification and operator-verified backup",
    )
    args = parser.parse_args()
    from app.config import get_settings
    from app.database import make_engine

    engine = make_engine(get_settings().database_url)
    with engine.begin() as connection:
        verify(connection)
        if args.stamp:
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.stamp(config, "0001_baseline")
    engine.dispose()
    print(
        "Legacy schema verified"
        + (" and explicitly stamped" if args.stamp else "; no changes made")
    )


if __name__ == "__main__":
    main()
