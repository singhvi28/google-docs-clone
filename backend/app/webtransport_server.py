"""
WebTransport (HTTP/3 / QUIC) collaboration server.

Routes:
  - Yjs text deltas  → reliable bidirectional streams
  - Awareness/cursors → unreliable datagrams

Shares Redis append-log + Pub/Sub with the WebSocket collab path so both
transports stay horizontally scalable. Requires TLS (browsers mandate it).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import uuid
from typing import Dict, Optional
from urllib.parse import parse_qs, urlparse

from aioquic.asyncio import QuicConnectionProtocol, serve
from aioquic.h3.connection import H3_ALPN, H3Connection
from aioquic.h3.events import (
    DatagramReceived,
    H3Event,
    HeadersReceived,
    WebTransportStreamDataReceived,
)
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import ProtocolNegotiated, QuicEvent, StreamReset
from sqlalchemy import select

from app.database import async_session_factory
from app.models import Document, DocumentPermission
from app.routes.auth import decode_access_token
from app.services.redis_service import (
    append_crdt_update,
    decrement_editor_count,
    get_merged_crdt_state,
    increment_editor_count,
    publish_update,
    remove_pending_editor,
    seed_crdt_state_if_empty,
    subscribe_to_document,
)
from app.utils import generate_cursor_color, generate_moniker

logger = logging.getLogger(__name__)

BIND_HOST = os.environ.get("WEBTRANSPORT_HOST", "0.0.0.0")
BIND_PORT = int(os.environ.get("WEBTRANSPORT_PORT", "4433"))


class CollabHandler:
    """Per-session WebTransport collaboration handler."""

    def __init__(
        self,
        session_id: int,
        http: H3Connection,
        protocol: "WebTransportProtocol",
        edit_key: str,
        token: str,
    ) -> None:
        self._session_id = session_id
        self._http = http
        self._protocol = protocol
        self._edit_key = edit_key
        self._token = token
        self._connection_id = str(uuid.uuid4())
        self._stream_buffers: Dict[int, bytearray] = {}
        self._server_stream_id: Optional[int] = None
        self._pubsub = None
        self._listener_task: Optional[asyncio.Task] = None
        self._closed = False
        self._ready = False
        asyncio.get_event_loop().create_task(self._bootstrap())

    async def _bootstrap(self) -> None:
        user_id = decode_access_token(self._token)
        if not user_id:
            self._close_session()
            return

        async with async_session_factory() as db:
            result = await db.execute(
                select(Document).where(Document.edit_key == self._edit_key)
            )
            doc = result.scalar_one_or_none()
            if not doc:
                self._close_session()
                return

            perm_result = await db.execute(
                select(DocumentPermission).where(
                    DocumentPermission.document_id == doc.id,
                    DocumentPermission.user_id == user_id,
                )
            )
            perm = perm_result.scalar_one_or_none()
            is_creator = str(doc.creator_id) == user_id
            if not perm and not is_creator:
                # Approval flow stays on WebSocket; WT requires prior permission
                self._close_session()
                return

            persisted = doc.crdt_state
            moniker = perm.moniker if perm else generate_moniker()
            color = perm.cursor_color if perm else generate_cursor_color()

        await increment_editor_count(self._edit_key)
        if persisted:
            await seed_crdt_state_if_empty(self._edit_key, persisted)

        self._server_stream_id = self._http.create_webtransport_stream(
            self._session_id, is_unidirectional=False
        )

        await self._send_stream({
            "type": "connected",
            "moniker": moniker,
            "color": color,
            "user_id": user_id,
        })

        merged = await get_merged_crdt_state(self._edit_key)
        if merged:
            await self._send_stream({
                "type": "sync_state",
                "data": base64.b64encode(merged).decode(),
            })

        await self._send_stream({"type": "sync_ready"})
        self._ready = True

        self._pubsub = await subscribe_to_document(self._edit_key)
        self._listener_task = asyncio.get_event_loop().create_task(self._redis_listen())

        await publish_update(
            self._edit_key,
            json.dumps({
                "type": "awareness_request",
                "_sid": self._connection_id,
            }).encode(),
        )

    async def _redis_listen(self) -> None:
        try:
            async for message in self._pubsub.listen():
                if self._closed or message["type"] != "message":
                    continue
                raw = message["data"]
                text = raw.decode() if isinstance(raw, bytes) else raw
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if parsed.get("_sid") == self._connection_id:
                    continue
                parsed.pop("_sid", None)
                msg_type = parsed.get("type")
                if msg_type == "awareness":
                    self._send_datagram(parsed)
                else:
                    await self._send_stream(parsed)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("WebTransport Redis listener error: %s", e)

    def h3_event_received(self, event: H3Event) -> None:
        if isinstance(event, DatagramReceived):
            # Unreliable path — awareness / cursor positions
            asyncio.get_event_loop().create_task(self._handle_datagram(event.data))
        elif isinstance(event, WebTransportStreamDataReceived):
            buf = self._stream_buffers.setdefault(event.stream_id, bytearray())
            buf.extend(event.data)
            # Newline-delimited JSON frames
            while b"\n" in buf:
                line, _, rest = bytes(buf).partition(b"\n")
                self._stream_buffers[event.stream_id] = bytearray(rest)
                if line:
                    asyncio.get_event_loop().create_task(self._handle_stream_message(line))
            if event.stream_ended:
                leftover = bytes(self._stream_buffers.pop(event.stream_id, b""))
                if leftover:
                    asyncio.get_event_loop().create_task(
                        self._handle_stream_message(leftover)
                    )

    async def _handle_datagram(self, data: bytes) -> None:
        if not self._ready:
            return
        try:
            msg = json.loads(data.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if msg.get("type") != "awareness":
            return
        await publish_update(
            self._edit_key,
            json.dumps({
                "type": "awareness",
                "data": msg.get("data"),
                "_sid": self._connection_id,
            }).encode(),
        )

    async def _handle_stream_message(self, raw: bytes) -> None:
        if not self._ready:
            return
        try:
            msg = json.loads(raw.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        msg_type = msg.get("type")
        if msg_type == "sync_update":
            try:
                update_data = base64.b64decode(msg["data"])
            except Exception:
                return
            await append_crdt_update(self._edit_key, update_data)
            await publish_update(
                self._edit_key,
                json.dumps({
                    "type": "sync_update",
                    "data": msg["data"],
                    "_sid": self._connection_id,
                }).encode(),
            )
            return

        # Creator approval controls (same semantics as WebSocket collab)
        if msg_type in ("approve_editor", "deny_editor"):
            user_id = decode_access_token(self._token)
            if not user_id:
                return
            async with async_session_factory() as db:
                result = await db.execute(
                    select(Document).where(Document.edit_key == self._edit_key)
                )
                doc = result.scalar_one_or_none()
                if not doc or str(doc.creator_id) != user_id:
                    return
                target_id = msg.get("user_id")
                if msg_type == "approve_editor" and target_id:
                    db.add(DocumentPermission(
                        document_id=doc.id,
                        user_id=target_id,
                        role="editor",
                        moniker=generate_moniker(),
                        cursor_color=generate_cursor_color(),
                    ))
                    await db.commit()
                    await remove_pending_editor(self._edit_key, target_id)
                elif msg_type == "deny_editor" and target_id:
                    await remove_pending_editor(self._edit_key, target_id)

    async def _send_stream(self, payload: dict) -> None:
        if self._server_stream_id is None:
            return
        data = (json.dumps(payload) + "\n").encode()
        self._http._quic.send_stream_data(self._server_stream_id, data, end_stream=False)
        self._protocol.transmit()

    def _send_datagram(self, payload: dict) -> None:
        self._http.send_datagram(self._session_id, json.dumps(payload).encode())
        self._protocol.transmit()

    def stream_closed(self, stream_id: int) -> None:
        self._stream_buffers.pop(stream_id, None)

    def _close_session(self) -> None:
        asyncio.get_event_loop().create_task(self.close())

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe(f"channel:{self._edit_key}")
                await self._pubsub.aclose()
            except Exception:
                pass
        count = await decrement_editor_count(self._edit_key)
        if count == 0:
            from app.routes.collab import flush_to_postgres
            await flush_to_postgres(self._edit_key)


class WebTransportProtocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._http: Optional[H3Connection] = None
        self._handler: Optional[CollabHandler] = None

    def quic_event_received(self, event: QuicEvent) -> None:
        if isinstance(event, ProtocolNegotiated):
            self._http = H3Connection(self._quic, enable_webtransport=True)
        elif isinstance(event, StreamReset) and self._handler is not None:
            self._handler.stream_closed(event.stream_id)

        if self._http is not None:
            for h3_event in self._http.handle_event(event):
                self._h3_event_received(h3_event)

    def _h3_event_received(self, event: H3Event) -> None:
        if isinstance(event, HeadersReceived):
            headers = {k: v for k, v in event.headers}
            if (
                headers.get(b":method") == b"CONNECT"
                and headers.get(b":protocol") == b"webtransport"
            ):
                self._handshake_webtransport(event.stream_id, headers)
            else:
                self._send_response(event.stream_id, 400, end_stream=True)

        if self._handler:
            self._handler.h3_event_received(event)

    def _handshake_webtransport(
        self, stream_id: int, request_headers: Dict[bytes, bytes]
    ) -> None:
        path_raw = request_headers.get(b":path", b"").decode()
        parsed = urlparse(path_raw)
        parts = [p for p in parsed.path.split("/") if p]
        # Expected: /wt/doc/{edit_key}
        if len(parts) != 3 or parts[0] != "wt" or parts[1] != "doc":
            self._send_response(stream_id, 404, end_stream=True)
            return

        edit_key = parts[2]
        qs = parse_qs(parsed.query)
        token = (qs.get("token") or [""])[0]

        self._handler = CollabHandler(
            stream_id, self._http, self, edit_key, token
        )
        self._send_response(stream_id, 200)

    def _send_response(
        self, stream_id: int, status_code: int, end_stream: bool = False
    ) -> None:
        headers = [(b":status", str(status_code).encode())]
        if status_code == 200:
            headers.append((b"sec-webtransport-http3-draft", b"draft02"))
        self._http.send_headers(
            stream_id=stream_id, headers=headers, end_stream=end_stream
        )


async def run_server(certfile: str, keyfile: str, host: str, port: int) -> None:
    configuration = QuicConfiguration(
        alpn_protocols=H3_ALPN,
        is_client=False,
        max_datagram_frame_size=65536,
    )
    configuration.load_cert_chain(certfile, keyfile)

    await serve(
        host,
        port,
        configuration=configuration,
        create_protocol=WebTransportProtocol,
    )
    logger.info("WebTransport listening on https://%s:%s (UDP)", host, port)
    await asyncio.Future()  # run forever


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="WebTransport collab server")
    parser.add_argument(
        "--certificate",
        default=os.environ.get("TLS_CERTFILE", "certs/cert.pem"),
    )
    parser.add_argument(
        "--key",
        default=os.environ.get("TLS_KEYFILE", "certs/key.pem"),
    )
    parser.add_argument("--host", default=BIND_HOST)
    parser.add_argument("--port", type=int, default=BIND_PORT)
    args = parser.parse_args()

    try:
        asyncio.run(run_server(args.certificate, args.key, args.host, args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
