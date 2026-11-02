"""One-use handshake and batched session authorization, durable invalidations.

HTTP messages remain authoritative across disconnected sockets/process restarts.
The short-lived ticket is sent as the first frame, never in a logged URL.
"""
import asyncio
import json
from datetime import timedelta
import secrets
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import or_, select, update
from starlette.concurrency import run_in_threadpool
from app.shared.config import settings
from app.shared.db import SessionLocal, utcnow
from app.shared.dependencies import DB, CurrentUser
from app.shared.models import AuthSession, User
from app.shared.security import digest
from app.modules.social.chat_models import ChatThread, ChatReadCursor, RealtimeTicket
from app.modules.social.chat_schemas import RealtimeTicketOut

router = APIRouter(tags=["realtime"])


async def text_frame(websocket):
    frame = await websocket.receive()
    if frame["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(frame.get("code", 1000))
    value = frame.get("text")
    if not isinstance(value, str) or len(value.encode("utf-8")) > 1024:
        raise ValueError("invalid_frame")
    return value


@router.post("/social/realtime-ticket", response_model=RealtimeTicketOut)
def create_ticket(request: Request, db: DB, user: CurrentUser):
    # Discard consumed/expired handshakes; they confer no history/access right.
    from sqlalchemy import delete
    db.execute(delete(RealtimeTicket).where(RealtimeTicket.user_id == user.id, or_(RealtimeTicket.used_at.is_not(None), RealtimeTicket.expires_at <= utcnow())))
    # At most one unused ticket per session: retries invalidate an older pending handshake.
    db.execute(delete(RealtimeTicket).where(RealtimeTicket.session_id == request.state.auth_session.id))
    raw = secrets.token_urlsafe(32)
    expiry = utcnow() + timedelta(seconds=30)
    db.add(RealtimeTicket(user_id=user.id, session_id=request.state.auth_session.id, token_hash=digest(raw), expires_at=expiry))
    db.commit()
    return dict(ticket=raw, expires_at=expiry)


def consume(raw):
    if not isinstance(raw, str) or not 20 <= len(raw) <= 100:
        return None
    with SessionLocal.begin() as db:
        row = db.scalar(select(RealtimeTicket).where(RealtimeTicket.token_hash == digest(raw), RealtimeTicket.used_at.is_(None), RealtimeTicket.expires_at > utcnow()).with_for_update())
        if not row:
            return None
        user, session = db.get(User, row.user_id), db.get(AuthSession, row.session_id)
        if not user or user.status != "active" or not session or session.user_id != user.id or session.revoked_at or session.expires_at <= utcnow():
            return None
        row.used_at = utcnow()
        return (user.id, session.id)


def snapshot(identity):
    user_id, session_id = identity
    with SessionLocal() as db:
        user, session = db.get(User, user_id), db.get(AuthSession, session_id)
        if not user or user.status != "active" or not session or session.user_id != user_id or session.revoked_at or session.expires_at <= utcnow():
            return None
        # Only sequence/cursor metadata is emitted. Bodies/attachments require HTTP auth.
        rows = db.execute(select(ChatThread.id, ChatThread.last_seq).where(or_(ChatThread.user_low_id == user_id, ChatThread.user_high_id == user_id))).all()
        return {str(row.id): row.last_seq for row in rows}


@router.websocket("/realtime/ws")
async def realtime(websocket: WebSocket):
    if websocket.headers.get("origin") != settings().app_origin:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        try:
            data = json.loads(await asyncio.wait_for(text_frame(websocket), timeout=5))
        except (asyncio.TimeoutError, ValueError, TypeError):
            await websocket.close(code=1008)
            return
        identity = await run_in_threadpool(consume, data.get("ticket") if isinstance(data, dict) else None)
        if not identity:
            await websocket.close(code=1008)
            return
        from app.modules.social.realtime_socket import serve
        hub = websocket.state.realtime_hub
        await hub.ensure_started()
        sub = hub.subscribe(identity)
        if sub is None:
            await websocket.close(code=1013)
            return
        await serve(websocket, hub, sub)
    except WebSocketDisconnect:
        return
