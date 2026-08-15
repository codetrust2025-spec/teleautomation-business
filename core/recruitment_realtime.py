"""Authenticated, replayable real-time transport for mail monitoring.

Operations code publishes small events here only after durable database writes.
The full email body is never included in a WebSocket payload.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("teleautomation.recruitment_realtime")

_connections: dict[WebSocket, dict[str, Any]] = {}
_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()


def configure_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def connection_count() -> int:
    return len(_connections)


async def connect(websocket: WebSocket, profile: dict[str, Any]) -> None:
    await websocket.accept()
    _connections[websocket] = profile


async def disconnect(websocket: WebSocket) -> None:
    _connections.pop(websocket, None)


async def _broadcast(payload: dict[str, Any]) -> None:
    dead: list[WebSocket] = []
    for websocket, profile in list(_connections.items()):
        if not profile.get("username"):
            dead.append(websocket)
            continue
        try:
            await websocket.send_json(payload)
        except Exception:
            dead.append(websocket)
    for websocket in dead:
        await disconnect(websocket)


def publish(event_type: str, **payload: Any) -> dict[str, Any]:
    """Persist an event, then fan it out when a server loop is available."""
    from core import recruitment_mail_store as store

    safe = {
        key: value
        for key, value in payload.items()
        if key not in {"body", "html_body", "raw_email", "credential_ciphertext"}
    }
    event = store.record_realtime_event(event_type, safe)
    envelope = {
        "event": event_type,
        "event_id": event["id"],
        **safe,
        "created_at": str(event.get("created_at") or safe.get("created_at") or ""),
    }
    loop = _loop
    if loop and loop.is_running():
        try:
            asyncio.run_coroutine_threadsafe(_broadcast(envelope), loop)
        except RuntimeError:
            logger.info("Mail WebSocket loop closed before event delivery event_id=%s", event["id"])
    return envelope
