"""Measurement only: event-loop lag and database pool use, every 5 s.

Writes DATA_DIR/lag-probe.log lines:
  time lag_max_ms lag_mean_ms pool_checked_out_max pool_size overflow_max
so a slow run shows whether requests wait for the event loop (lag) or for a
pooled connection (checked out at the pool's limit).
"""

import asyncio
import os
import time

from app.config import settings
from app.database import engine


async def _run() -> None:
    loop = asyncio.get_running_loop()
    path = os.path.join(settings.DATA_DIR, "lag-probe.log")
    pool = engine.sync_engine.pool
    with open(path, "a", buffering=1) as out:
        out.write("time lag_max_ms lag_mean_ms checked_out_max pool_size overflow_max\n")
        lags: list[float] = []
        checked, overflow = 0, 0
        window = loop.time()
        while True:
            start = loop.time()
            await asyncio.sleep(0.1)
            lags.append(max(0.0, loop.time() - start - 0.1))
            try:
                checked = max(checked, pool.checkedout())
                overflow = max(overflow, pool.overflow())
            except Exception:  # pragma: no cover - pool without counters
                pass
            if loop.time() - window >= 5:
                out.write(
                    f"{time.strftime('%H:%M:%S', time.gmtime())} {1000 * max(lags):.0f} "
                    f"{1000 * sum(lags) / len(lags):.0f} {checked} "
                    f"{getattr(pool, 'size', lambda: 0)()} {overflow}\n"
                )
                lags, checked, overflow, window = [], 0, 0, loop.time()


def start() -> asyncio.Task | None:
    return asyncio.get_running_loop().create_task(_run())
