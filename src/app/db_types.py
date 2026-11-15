"""Column types that behave the same on SQLite and PostgreSQL."""

from datetime import timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """A ``DateTime`` stored as naive UTC, whatever the value passed in.

    The code mixes ``datetime.now(timezone.utc)`` (aware) and naive UTC
    values. SQLite drops the offset when it stores either, so both work;
    asyncpg refuses an aware value for a ``timestamp without time zone``
    column. Converting to UTC and dropping the offset here gives every
    database what SQLite stored.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
