"""Bounded socket transport; all authorization rechecks are hub batches."""
import asyncio
from contextlib import suppress
import json
import time
from fastapi import WebSocketDisconnect
from anyio import CancelScope


async def text_frame(socket, *, max_bytes=1024):
    frame = await socket.receive()
    if frame["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(frame.get("code", 1000))
    value = frame.get("text")
    if not isinstance(value, str) or len(value.encode("utf-8")) > max_bytes:
        raise ValueError("invalid_frame")
    return value


async def receive_pings(socket):
    # A ping carries no data/capability and must never touch the database.
    tokens, checked = 4.0, time.monotonic()
    while True:
        value = await text_frame(socket, max_bytes=128)
        now = time.monotonic()
        tokens = min(4, tokens + (now - checked) * 1.0)
        checked = now
        if tokens < 1:
            raise ValueError("frame_rate")
        tokens -= 1
        if value == "ping":  # legacy clients; same byte/rate budget, no SQL
            continue
        data = json.loads(value)
        if data != {"type": "ping"}:
            raise ValueError("invalid_ping")


async def send_frames(socket, hub, sub):
    while True:
        try:
            frame = await asyncio.wait_for(sub.frames.get(), 15)
        except asyncio.TimeoutError:
            frame = hub.frame("heartbeat")
        if frame["type"] == "close":
            await asyncio.wait_for(socket.close(code=frame["code"]), 2)
            return
        await asyncio.wait_for(socket.send_json(frame), 2)


async def serve(socket, hub, sub):
    sender = asyncio.create_task(send_frames(socket, hub, sub))
    receiver = asyncio.create_task(receive_pings(socket))
    try:
        done, _ = await asyncio.wait((sender, receiver), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except asyncio.CancelledError:
        pass  # ASGI request cancellation is a normal disconnected transport.
    except (WebSocketDisconnect, asyncio.TimeoutError, ValueError, TypeError):
        with suppress(Exception):
            await asyncio.wait_for(socket.close(code=1008), 2)
    finally:
        hub.unsubscribe(sub)
        for task in (sender, receiver):
            task.cancel()
        with CancelScope(shield=True):
            await asyncio.gather(sender, receiver, return_exceptions=True)
