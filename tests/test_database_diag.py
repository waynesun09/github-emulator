"""The writer-lock logging names the holder and how long a lock error waited."""

import asyncio
import logging
import sqlite3

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import create_async_engine

from app.database_diag import current_request, instrument_sqlite_engine


@pytest.mark.asyncio
async def test_logs_a_slow_writer_and_a_lock_wait(tmp_path, caplog, monkeypatch):
    monkeypatch.setattr("app.database_diag.settings.SQLITE_SLOW_WRITE_LOG_MS", 50)
    path = tmp_path / "d.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", connect_args={"timeout": 0.2})
    instrument_sqlite_engine(engine)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE t (x INTEGER)"))
    caplog.set_level(logging.WARNING, logger="github_emulator.database")

    token = current_request.set("PUT /api/v3/repos/o/r/contents/f")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO t VALUES (1)"))
            await asyncio.sleep(0.1)  # slow work while holding the lock
    finally:
        current_request.reset(token)
    assert "writer lock held" in caplog.text
    assert "PUT /api/v3/repos/o/r/contents/f" in caplog.text
    assert "INSERT INTO t VALUES" in caplog.text
    assert "waited" in caplog.text

    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(OperationalError):
            async with engine.begin() as conn:
                await conn.execute(text("INSERT INTO t VALUES (2)"))
    finally:
        holder.rollback()
        holder.close()
    assert "database is locked after" in caplog.text
    await engine.dispose()


@pytest.mark.asyncio
async def test_waiting_for_the_lock_is_not_counted_as_holding_it(tmp_path, caplog, monkeypatch):
    import threading

    monkeypatch.setattr("app.database_diag.settings.SQLITE_SLOW_WRITE_LOG_MS", 150)
    path = tmp_path / "w.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", connect_args={"timeout": 5})
    instrument_sqlite_engine(engine)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE t (x INTEGER)"))
    caplog.set_level(logging.WARNING, logger="github_emulator.database")

    holder = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    threading.Timer(0.4, holder.rollback).start()
    async with engine.begin() as conn:  # waits about 0.4 s, then holds briefly
        await conn.execute(text("INSERT INTO t VALUES (1)"))
    holder.close()
    assert "writer lock held" not in caplog.text
    await engine.dispose()
