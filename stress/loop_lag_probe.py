#!/usr/bin/env python3
"""
Sample asyncio event-loop lag against a running backend.

Periodically schedules a no-op callback and measures scheduling delay.
Rising lag under load usually means synchronous work (e.g. heavy pycrdt merge)
is blocking the loop.

Usage:
  backend/.venv/bin/python stress/loop_lag_probe.py --url http://127.0.0.1:8000/api/health
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import time

import httpx


async def probe_http(url: str, samples: int, interval: float) -> None:
    lags_ms: list[float] = []
    async with httpx.AsyncClient(timeout=5.0) as client:
        for i in range(samples):
            loop = asyncio.get_running_loop()
            t0 = time.perf_counter()
            fut: asyncio.Future[float] = loop.create_future()

            def _cb(start: float = t0, future: asyncio.Future[float] = fut) -> None:
                if not future.done():
                    future.set_result((time.perf_counter() - start) * 1000)

            loop.call_soon(_cb)
            # Concurrently hit health to keep the server busy if Locust is running
            try:
                await client.get(url)
            except Exception:
                pass
            lag = await fut
            lags_ms.append(lag)
            print(f"  sample {i + 1}: loop-schedule lag {lag:.3f} ms")
            await asyncio.sleep(interval)

    print()
    print(f"client-side schedule lag avg={statistics.mean(lags_ms):.3f} ms  "
          f"max={max(lags_ms):.3f} ms")
    print("(This probes the local event loop of this script; for server lag,")
    print(" watch reflector misses + metrics.sh CPU while Locust runs.)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/health")
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args()
    asyncio.run(probe_http(args.url, args.samples, args.interval))


if __name__ == "__main__":
    main()
