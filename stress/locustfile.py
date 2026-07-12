"""
Locust stress client for collaborative editor.

Uses synchronous WebSockets (compatible with Locust/gevent) through the LB
or a direct instance. Validates Redis Pub/Sub fan-out under concurrent editors.

Prerequisites:
  1. Dual backends (+ optional nginx)
  2. seed_stress_session.py → stress/.session.env

Run:
  set -a && source stress/.session.env && set +a
  locust -f locustfile.py --host http://localhost:8000
  locust -f locustfile.py --host http://localhost:8080 --headless -u 20 -r 5 -t 30s
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

from locust import User, between, events, task
from locust.exception import StopUser

try:
    from websockets.sync.client import connect as ws_connect
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Install stress deps: pip install -r stress/requirements.txt") from exc


SESSION_FILE = Path(__file__).resolve().parent / ".session.env"


def _load_session_file() -> None:
    if not SESSION_FILE.exists():
        return
    for line in SESSION_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_session_file()


def _require_env(*keys: str) -> dict[str, str]:
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            f"Missing {missing}. Run: backend/.venv/bin/python stress/seed_stress_session.py"
        )
    return {k: os.environ[k] for k in keys}


def _make_delta_b64(counter: int) -> str:
    try:
        from pycrdt import Doc, Text

        doc = Doc()
        text = doc.get("content", type=Text)
        text.insert(0, f"k{counter}")
        return base64.b64encode(bytes(doc.get_update())).decode()
    except Exception:
        sample = os.environ.get("STRESS_SAMPLE_UPDATE", "")
        if not sample:
            raise
        return sample


def _http_to_ws(url: str) -> str:
    return url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")


class CollabWebSocketUser(User):
    """Simulates an editor typing over WebSocket."""

    wait_time = between(0.05, 0.25)

    def on_start(self):
        env = _require_env("STRESS_TOKEN", "STRESS_EDIT_KEY")
        self.token = env["STRESS_TOKEN"]
        self.edit_key = env["STRESS_EDIT_KEY"]
        self.ws_base = _http_to_ws(
            self.host
            or os.environ.get("STRESS_WS_BASE")
            or os.environ.get("STRESS_API_BASE", "http://127.0.0.1:8000")
        )
        self._counter = 0
        url = f"{self.ws_base}/ws/doc/{self.edit_key}?token={self.token}"
        self._ws = ws_connect(url, max_size=8 * 1024 * 1024, open_timeout=15)
        deadline = time.time() + 15
        while time.time() < deadline:
            raw = self._ws.recv(timeout=15)
            msg = json.loads(raw)
            if msg.get("type") == "sync_ready":
                return
            if msg.get("type") in ("room_full", "approval_denied"):
                self._ws.close()
                raise StopUser()
        self._ws.close()
        raise StopUser()

    def on_stop(self):
        ws = getattr(self, "_ws", None)
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    @task
    def send_sync_update(self):
        self._counter += 1
        payload = json.dumps(
            {"type": "sync_update", "data": _make_delta_b64(self._counter)}
        )
        start = time.perf_counter()
        exception = None
        try:
            self._ws.send(payload)
        except Exception as exc:  # noqa: BLE001
            exception = exc
        total_ms = (time.perf_counter() - start) * 1000
        self.environment.events.request.fire(
            request_type="WebSocket",
            name="send_sync_update",
            response_time=total_ms,
            response_length=len(payload),
            exception=exception,
            context={},
        )

    @task(3)
    def send_awareness(self):
        payload = json.dumps(
            {
                "type": "awareness",
                "data": os.environ.get("STRESS_SAMPLE_UPDATE", "YQ=="),
            }
        )
        start = time.perf_counter()
        exception = None
        try:
            self._ws.send(payload)
        except Exception as exc:  # noqa: BLE001
            exception = exc
        total_ms = (time.perf_counter() - start) * 1000
        self.environment.events.request.fire(
            request_type="WebSocket",
            name="send_awareness",
            response_time=total_ms,
            response_length=len(payload),
            exception=exception,
            context={},
        )


@events.test_start.add_listener
def _on_test_start(environment, **kwargs):
    try:
        _require_env("STRESS_TOKEN", "STRESS_EDIT_KEY")
    except RuntimeError as exc:
        environment.runner.quit()
        raise SystemExit(str(exc)) from exc
