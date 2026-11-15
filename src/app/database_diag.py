"""Log who holds SQLite's writer lock, and for how long.

SQLite has one writer at a time. A write transaction that stays open while
its request does slow work (a git subprocess, an outbound HTTP call) makes
every other writer wait, and past the busy timeout they fail with
"database is locked" (answered 503). These events log:

- a write transaction that held the lock longer than
  ``SQLITE_SLOW_WRITE_LOG_MS``, with the request, its first write, and how
  long that first write waited for the lock. Held time runs from the first
  write's completion (the lock is then taken) to the start of the commit,
  so waiting for the lock is not counted as holding it;
- each "database is locked" error, with how long the statement waited
  (about the busy timeout: a real wait; about zero: a snapshot conflict).

Logger ``github_emulator.database``. Only the timing and the first SQL
words are logged, never parameters.
"""

import contextvars
import logging
import time

from sqlalchemy import event

from app.config import settings

logger = logging.getLogger("github_emulator.database")

# "METHOD /path" of the request using the connection, set by the middleware.
current_request: contextvars.ContextVar[str] = contextvars.ContextVar("current_request", default="-")

_WRITES = ("INSERT", "UPDATE", "DELETE", "REPLACE")


def _head(statement: str) -> str:
    return " ".join(statement.split()[:6])[:120]


def instrument_sqlite_engine(engine) -> None:
    """Attach the writer-lock timing events to a SQLite engine."""
    sync = engine.sync_engine
    if sync.dialect.name != "sqlite":
        return

    @event.listens_for(sync, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        conn.info["diag_statement_start"] = time.monotonic()

    @event.listens_for(sync, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        if "diag_write_start" in conn.info or not statement.lstrip()[:7].upper().startswith(_WRITES):
            return
        now = time.monotonic()
        conn.info["diag_write_start"] = now
        conn.info["diag_write_waited"] = now - conn.info.get("diag_statement_start", now)
        conn.info["diag_write_first"] = _head(statement)
        conn.info["diag_write_request"] = current_request.get()

    def _end(conn):
        start = conn.info.pop("diag_write_start", None)
        waited = conn.info.pop("diag_write_waited", 0.0)
        first = conn.info.pop("diag_write_first", "")
        request = conn.info.pop("diag_write_request", "-")
        if start is None:
            return
        held_ms = (time.monotonic() - start) * 1000
        if held_ms >= settings.SQLITE_SLOW_WRITE_LOG_MS:
            logger.warning(
                "writer lock held %.0f ms by %s (first write: %s; it waited %.0f ms for the lock)",
                held_ms, request, first, waited * 1000,
            )

    event.listen(sync, "commit", _end)
    event.listen(sync, "rollback", _end)

    @event.listens_for(sync, "handle_error")
    def _locked(context):
        if "database is locked" not in str(context.original_exception).lower():
            return
        conn = context.connection
        start = conn.info.get("diag_statement_start") if conn is not None else None
        waited = f"{(time.monotonic() - start) * 1000:.0f} ms" if start else "?"
        logger.warning(
            "database is locked after %s for %s (%s)",
            waited, current_request.get(), _head(context.statement or ""),
        )


class RequestContextMiddleware:
    """Record "METHOD /path" for the database logging above."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = current_request.set(f"{scope['method']} {scope['path']}")
        try:
            await self.app(scope, receive, send)
        finally:
            current_request.reset(token)
