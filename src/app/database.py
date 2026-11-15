from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


def _pool_args(url: str) -> dict:
    """Pool sizing for a file-backed SQLite database.

    An in-memory database uses a single shared connection (StaticPool),
    which takes no size.
    """
    if url.startswith("sqlite") and ":memory:" not in url and "mode=memory" not in url:
        return {
            "pool_size": settings.SQLITE_POOL_SIZE,
            "max_overflow": settings.SQLITE_MAX_OVERFLOW,
        }
    return {}


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args={
        "check_same_thread": False,
        "timeout": settings.SQLITE_BUSY_TIMEOUT_MS / 1000,
    },
    **_pool_args(settings.DATABASE_URL),
)


@event.listens_for(engine.sync_engine, "connect")
def _configure_sqlite_connection(dbapi_connection, connection_record):
    """Apply SQLite pragmas to every pooled connection."""
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={settings.SQLITE_BUSY_TIMEOUT_MS}")
        if settings.SQLITE_SYNCHRONOUS:
            mode = settings.SQLITE_SYNCHRONOUS.upper()
            if mode not in ("OFF", "NORMAL", "FULL", "EXTRA"):
                raise ValueError(f"SQLITE_SYNCHRONOUS={mode} is not OFF, NORMAL, FULL or EXTRA")
            cursor.execute(f"PRAGMA synchronous={mode}")
    finally:
        cursor.close()
    if settings.SQLITE_BEGIN_IMMEDIATE:
        # The driver would begin its own (deferred) transaction before the
        # first write; SQLAlchemy's begin event below starts it instead.
        dbapi_connection.isolation_level = None


_WRITE_METHODS = ("POST ", "PUT ", "PATCH ", "DELETE ")


@event.listens_for(engine.sync_engine, "begin")
def _begin_sqlite_transaction(conn):
    """With SQLITE_BEGIN_IMMEDIATE, start each transaction explicitly.

    A write request takes the write lock up front (BEGIN IMMEDIATE), so it
    waits for it once, before any work, and never has to upgrade a read
    snapshot; any other request begins a deferred transaction.
    """
    if not settings.SQLITE_BEGIN_IMMEDIATE or not settings.DATABASE_URL.startswith("sqlite"):
        return
    from app.database_diag import current_request

    request = current_request.get() or ""
    conn.exec_driver_sql("BEGIN IMMEDIATE" if request.startswith(_WRITE_METHODS) else "BEGIN")

from app.database_diag import instrument_sqlite_engine  # noqa: E402

instrument_sqlite_engine(engine)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Upgrade the schema and configure the SQLite connection policy."""
    from app.migrations import upgrade_database

    await upgrade_database(settings.DATABASE_URL)
    async with engine.begin() as conn:
        if settings.DATABASE_URL.startswith("sqlite"):
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text(f"PRAGMA busy_timeout={settings.SQLITE_BUSY_TIMEOUT_MS}"))
