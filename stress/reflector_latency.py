#!/usr/bin/env python3
"""
E2E broadcast latency reflector.

Connects Client_A to instance 1 and Client_B to instance 2 on the same edit_key,
sends timestamped sync_update messages from A, and measures when B receives them
via Redis Pub/Sub fan-out.

This is the most accurate way to measure horizontal-scale latency for this stack:
  Instance1 → Redis PUBLISH → Instance2 → Client_B

Usage:
  set -a && source stress/.session.env && set +a
  backend/.venv/bin/python stress/reflector_latency.py
  backend/.venv/bin/python stress/reflector_latency.py --rounds 100 --interval 0.05
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import statistics
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SESSION_FILE = ROOT / "stress" / ".session.env"


def _load_session() -> None:
    if not SESSION_FILE.exists():
        return
    for line in SESSION_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _require(*keys: str) -> None:
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        raise SystemExit(
            f"Missing {missing}. Run seed_stress_session.py first."
        )


def _marker_payload(marker: str) -> str:
    """Encode a unique marker as base64 (server treats it as opaque CRDT bytes)."""
    return base64.b64encode(f"STRESS_MARKER:{marker}".encode()).decode()


async def _wait_ready(ws, label: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        msg = json.loads(raw)
        if msg.get("type") == "sync_ready":
            print(f"  [{label}] sync_ready")
            return
        if msg.get("type") in ("room_full", "approval_denied", "pending_approval"):
            raise RuntimeError(f"{label} blocked: {msg}")
    raise TimeoutError(f"{label} did not receive sync_ready")


async def _reader(ws, queue: asyncio.Queue, label: str) -> None:
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "sync_update":
                await queue.put((time.perf_counter(), msg))
    except Exception as exc:  # noqa: BLE001
        print(f"  [{label}] reader stopped: {exc}")


async def run_reflector(rounds: int, interval: float) -> None:
    import websockets

    _load_session()
    _require("STRESS_TOKEN", "STRESS_EDIT_KEY")

    token = os.environ["STRESS_TOKEN"]
    edit_key = os.environ["STRESS_EDIT_KEY"]
    a_base = os.environ.get("STRESS_INSTANCE1_WS", "ws://127.0.0.1:8000").rstrip("/")
    b_base = os.environ.get("STRESS_INSTANCE2_WS", "ws://127.0.0.1:8001").rstrip("/")

    url_a = f"{a_base}/ws/doc/{edit_key}?token={token}"
    url_b = f"{b_base}/ws/doc/{edit_key}?token={token}"

    print("Reflector latency test")
    print(f"  Client_A → {url_a}")
    print(f"  Client_B → {url_b}")
    print(f"  rounds={rounds} interval={interval}s")
    print()

    async with websockets.connect(url_a, max_size=8 * 1024 * 1024) as ws_a, \
            websockets.connect(url_b, max_size=8 * 1024 * 1024) as ws_b:
        await asyncio.gather(_wait_ready(ws_a, "A"), _wait_ready(ws_b, "B"))

        queue_b: asyncio.Queue = asyncio.Queue()
        reader_b = asyncio.create_task(_reader(ws_b, queue_b, "B"))
        # Drain A's own inbound (awareness etc.) so buffers don't fill
        queue_a: asyncio.Queue = asyncio.Queue()
        reader_a = asyncio.create_task(_reader(ws_a, queue_a, "A"))

        # Let subscriptions settle
        await asyncio.sleep(0.3)

        latencies_ms: list[float] = []
        misses = 0

        for i in range(rounds):
            marker = f"{uuid.uuid4().hex}:{time.time_ns()}"
            payload = json.dumps(
                {"type": "sync_update", "data": _marker_payload(marker)}
            )
            t0 = time.perf_counter()
            await ws_a.send(payload)

            matched = False
            deadline = t0 + 5.0
            while time.perf_counter() < deadline:
                timeout = max(0.01, deadline - time.perf_counter())
                try:
                    t1, msg = await asyncio.wait_for(queue_b.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    break
                data = msg.get("data") or ""
                try:
                    decoded = base64.b64decode(data).decode(errors="ignore")
                except Exception:
                    continue
                if marker in decoded:
                    latencies_ms.append((t1 - t0) * 1000)
                    matched = True
                    break

            if not matched:
                misses += 1
                print(f"  round {i + 1}: MISS")
            else:
                print(f"  round {i + 1}: {latencies_ms[-1]:.2f} ms")

            await asyncio.sleep(interval)

        reader_a.cancel()
        reader_b.cancel()

    print()
    if not latencies_ms:
        print("No successful round-trips — check that both instances share Redis.")
        sys.exit(1)

    latencies_ms.sort()
    p50 = statistics.median(latencies_ms)
    p95 = latencies_ms[min(len(latencies_ms) - 1, int(len(latencies_ms) * 0.95))]
    p99 = latencies_ms[min(len(latencies_ms) - 1, int(len(latencies_ms) * 0.99))]

    print("Results (Client_A → Redis Pub/Sub → Client_B)")
    print(f"  samples: {len(latencies_ms)}  misses: {misses}")
    print(f"  min:  {min(latencies_ms):.2f} ms")
    print(f"  avg:  {statistics.mean(latencies_ms):.2f} ms")
    print(f"  p50:  {p50:.2f} ms")
    print(f"  p95:  {p95:.2f} ms")
    print(f"  p99:  {p99:.2f} ms")
    print(f"  max:  {max(latencies_ms):.2f} ms")


def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-instance E2E latency reflector")
    parser.add_argument("--rounds", type=int, default=50)
    parser.add_argument("--interval", type=float, default=0.05)
    args = parser.parse_args()
    asyncio.run(run_reflector(args.rounds, args.interval))


if __name__ == "__main__":
    main()
