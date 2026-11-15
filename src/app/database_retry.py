"""Helpers for bounded SQLite write-contention handling."""

import asyncio
import logging
from typing import Any, Awaitable, Callable

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.middleware.error_handler import RetryableDatabaseError

logger = logging.getLogger("github_emulator.database")


# PostgreSQL's "try the transaction again" codes: serialization_failure and
# deadlock_detected.
_RETRY_SQLSTATES = {"40001", "40P01"}


def is_sqlite_database_locked(exc: BaseException) -> bool:
    """Return True when a write failed only because another one won.

    SQLite: its writer lock was held past the busy timeout. PostgreSQL: a
    serialization failure or deadlock, where the transaction can simply run
    again. Callers roll back and replay, or answer 503 with Retry-After.
    """
    if not isinstance(exc, DBAPIError):
        return False
    if getattr(exc.orig, "sqlstate", None) in _RETRY_SQLSTATES:
        return True
    return "database is locked" in str(exc).lower()


async def rollback_after_sqlite_lock(db: AsyncSession, exc: BaseException) -> bool:
    """Rollback a session if *exc* is a SQLite lock and report whether handled."""
    if not is_sqlite_database_locked(exc):
        return False
    await db.rollback()
    return True


async def commit_with_sqlite_retry(
    db: AsyncSession,
    *,
    label: str,
    before_retry: Callable[[], Any | Awaitable[Any]] | None = None,
) -> None:
    """Commit a replayable write transaction with bounded SQLite-lock retry.

    Callers that mutate ORM state must provide ``before_retry`` to replay that
    mutation after rollback. Non-replayable transactions intentionally surface
    a retryable error instead of treating a second empty commit as success.
    """
    attempts = max(1, settings.SQLITE_WRITE_RETRY_ATTEMPTS)
    delay = max(0, settings.SQLITE_WRITE_RETRY_DELAY_MS) / 1000

    for attempt in range(1, attempts + 1):
        try:
            await db.commit()
            return
        except DBAPIError as exc:
            if not is_sqlite_database_locked(exc):
                raise
            await db.rollback()
            if attempt >= attempts:
                logger.warning(
                    "SQLite write lock persisted after %d attempts during %s",
                    attempts,
                    label,
                )
                raise RetryableDatabaseError() from exc
            logger.info(
                "SQLite write lock during %s; retrying attempt %d/%d",
                label,
                attempt + 1,
                attempts,
            )
            if delay:
                await asyncio.sleep(delay)
            if before_retry is not None:
                result = before_retry()
                if asyncio.iscoroutine(result):
                    await result
            else:
                raise RetryableDatabaseError(
                    "Database was busy and the write cannot be replayed safely"
                ) from exc
