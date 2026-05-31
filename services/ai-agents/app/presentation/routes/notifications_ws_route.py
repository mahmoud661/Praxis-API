"""
Per-user notifications WebSocket. Mounted at /ws/notifications.

Subscribed to the Redis pub/sub channel `users:{owner_id}:notif`. Receives
`run.started` / `run.ended` lifecycle pings and forwards them to the
client. The sidebar uses this to flip a "running" indicator across every
tab/device the user has open — no polling needed.

Pub/sub (not Streams) is the right choice here because:
  - Late joiners do NOT need replay; the sidebar hydrates its current
    state from `GET /v1/agents/runs/active` on connection.
  - Notifications are tiny one-shot events; no buffering needed.
"""

from __future__ import annotations

import json

from fastapi import WebSocket, WebSocketDisconnect
from redis.asyncio import Redis

from ..http.dependencies import ws_authenticate
from .base_route import BaseRoute


class NotificationsWsRoute(BaseRoute):
    path = "/ws"

    def __init__(self, redis: Redis) -> None:
        # Container resolves `redis: Redis` from token "Redis".
        self._redis = redis
        super().__init__()

    def _init_routes(self) -> None:
        self.router.add_api_websocket_route(
            "/notifications", self._handle
        )

    async def _handle(self, ws: WebSocket) -> None:
        user_id = await ws_authenticate(ws)
        if user_id is None:
            return

        await ws.accept()
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(f"users:{user_id}:notif")
        try:
            # `get_message` with a short timeout lets a client-initiated
            # disconnect propagate quickly through the `ws.send_json` call
            # below (Starlette only notices disconnect when we try to write).
            while True:
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=5.0
                )
                if msg is None:
                    continue
                if msg.get("type") != "message":
                    continue
                raw = msg.get("data")
                if raw is None:
                    continue
                try:
                    payload = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                await ws.send_json(payload)
        except WebSocketDisconnect:
            # Client closed the socket — expected lifecycle event for
            # the sidebar notifications stream. No diagnostic value.
            pass
        finally:
            try:
                await pubsub.unsubscribe(f"users:{user_id}:notif")
                await pubsub.close()
            except Exception:  # noqa: BLE001
                pass
