"""Portable persistence types; Oracle stores JSON documents in CLOBs."""

import json
from datetime import UTC

from sqlalchemy import JSON, DateTime, Text
from sqlalchemy.dialects.oracle import TIMESTAMP
from sqlalchemy.types import TypeDecorator


class Document(TypeDecorator):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(Text() if dialect.name == "oracle" else JSON())

    def process_bind_param(self, value, dialect):
        if dialect.name == "oracle" and value is not None:
            return json.dumps(value, ensure_ascii=False, allow_nan=False)
        return value

    def process_result_value(self, value, dialect):
        if dialect.name == "oracle" and value is not None:
            return json.loads(value.read() if hasattr(value, "read") else value)
        return value


class NotesText(TypeDecorator):
    """Oracle treats empty strings as NULL; expose blank notes consistently to callers."""

    impl = Text
    cache_ok = True

    def process_result_value(self, value, dialect):
        return value if value is not None else ""


class UtcTimestamp(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(
            TIMESTAMP(timezone=True) if dialect.name == "oracle" else DateTime(timezone=True)
        )

    def process_bind_param(self, value, dialect):
        if value is not None:
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return value
