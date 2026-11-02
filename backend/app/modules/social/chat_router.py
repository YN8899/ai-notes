from uuid import UUID
from urllib.parse import quote
from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from app.shared.dependencies import DB, CurrentUser
from app.shared.db import utcnow
from app.shared.errors import AppError
from app.modules.social import chat_schemas as s, chat_service as service
from app.modules.social.chat_models import ChatAttachment, ChatMessage, ChatReadCursor, ChatThread

router = APIRouter(prefix="/social/chat", tags=["private-chat"])


@router.get("/summary", response_model=s.ChatSummary)
def summary(db: DB, user: CurrentUser):
    participant = or_(ChatThread.user_low_id == user.id, ChatThread.user_high_id == user.id)
    unread = select(ChatMessage.thread_id).join(ChatThread, ChatThread.id == ChatMessage.thread_id).outerjoin(ChatReadCursor, and_(ChatReadCursor.thread_id == ChatThread.id, ChatReadCursor.user_id == user.id)).where(participant, ChatMessage.sender_id != user.id, ChatMessage.seq > func.coalesce(ChatReadCursor.last_read_seq, 0)).subquery()
    return dict(threads=db.scalar(select(func.count()).select_from(ChatThread).where(participant)), unread_messages=db.scalar(select(func.count()).select_from(unread)), unread_threads=db.scalar(select(func.count(func.distinct(unread.c.thread_id)))))


@router.post("/threads", response_model=s.ThreadOut, status_code=201)
def create_thread(data: s.ThreadCreate, db: DB, user: CurrentUser):
    row = service.create_thread(db, user, data.user_id)
    from app.modules.social.realtime_signal import publish
    for owner_id in (row.user_low_id, row.user_high_id):
        publish(db, owner_id, "chat")
    db.commit()
    return service.thread_output(db, row, user)


@router.get("/threads", response_model=s.ThreadList)
def threads(db: DB, user: CurrentUser, cursor: UUID | None = None, limit: int = Query(30, ge=1, le=100)):
    stmt = select(ChatThread).where(or_(ChatThread.user_low_id == user.id, ChatThread.user_high_id == user.id))
    if cursor:
        stmt = stmt.where(ChatThread.id > cursor)
    rows = db.scalars(stmt.order_by(ChatThread.id).limit(limit + 1)).all()
    return dict(items=[service.thread_output(db, row, user) for row in rows[:limit]], next_cursor=str(rows[limit-1].id) if len(rows) > limit else None)


@router.get("/threads/{thread_id}", response_model=s.ThreadOut)
def get_thread(thread_id: UUID, db: DB, user: CurrentUser):
    return service.thread_output(db, service.thread(db, user, thread_id), user)


@router.get("/threads/{thread_id}/messages", response_model=s.MessageList)
def messages(thread_id: UUID, db: DB, user: CurrentUser, after_seq: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    service.thread(db, user, thread_id)
    rows = db.scalars(select(ChatMessage).where(ChatMessage.thread_id == thread_id, ChatMessage.seq > after_seq).order_by(ChatMessage.seq).limit(limit + 1)).all()
    visible = rows[:limit]
    return dict(items=[service.message_output(db, row) for row in visible], next_after=visible[-1].seq if visible else after_seq, has_more=len(rows) > limit)


@router.post("/threads/{thread_id}/messages", response_model=s.MessageOut, status_code=201)
def send(thread_id: UUID, data: s.MessageCreate, db: DB, user: CurrentUser):
    row = service.send(db, user, thread_id, data)
    db.commit()
    return service.message_output(db, row)


@router.post("/threads/{thread_id}/read", response_model=s.ReadOut)
def read(thread_id: UUID, data: s.ReadIn, db: DB, user: CurrentUser):
    row = service.thread(db, user, thread_id, lock=True)
    if data.seq > row.last_seq:
        raise AppError(409, "read_cursor_ahead", "已读位置超过现有消息")
    stmt = insert(ChatReadCursor).values(thread_id=row.id, user_id=user.id, last_read_seq=data.seq)
    stmt = stmt.on_conflict_do_update(index_elements=[ChatReadCursor.thread_id, ChatReadCursor.user_id], set_={"last_read_seq": data.seq}, where=ChatReadCursor.last_read_seq < data.seq)
    changed = db.execute(stmt)
    if changed.rowcount:
        from app.modules.social.realtime_signal import publish
        for owner_id in (row.user_low_id, row.user_high_id):
            publish(db, owner_id, "chat")
    db.commit()
    value = db.scalar(select(ChatReadCursor.last_read_seq).where(ChatReadCursor.thread_id == row.id, ChatReadCursor.user_id == user.id))
    return dict(last_read_seq=value)


@router.put("/threads/{thread_id}/attachments", response_model=s.AttachmentOut, status_code=201,
    openapi_extra={"requestBody": {"required": True, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
async def upload(thread_id: UUID, request: Request, db: DB, user: CurrentUser, filename: str = Query(min_length=1, max_length=200), request_key: str = Query(min_length=1, max_length=64)):
    # Bound each stream before loading content, including clients without Content-Length.
    # Preflight does not lock a relationship before the body is received. The
    # service rechecks under thread -> relationship locks after bounded upload.
    if not service.can_send(db, service.thread(db, user, thread_id), user):
        raise AppError(403, "friend_required", "只有当前好友可以发送附件")
    content = bytearray()
    async for block in request.stream():
        content.extend(block)
        if len(content) > 10 * 1024 * 1024:
            raise AppError(413, "attachment_size", "附件不能超过10 MiB")
    attachment = service.upload_attachment(db, user, thread_id, bytes(content), filename, request_key)
    db.commit()
    return attachment


@router.get("/attachments/{attachment_id}/content", response_class=Response,
    responses={200: {"content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
def attachment(attachment_id: UUID, db: DB, user: CurrentUser):
    row = db.get(ChatAttachment, attachment_id)
    if not row:
        raise AppError(404, "attachment_not_found", "附件不存在")
    service.thread(db, user, row.thread_id)
    if row.message_id is None and row.owner_id != user.id:
        raise AppError(404, "attachment_not_found", "附件尚未发送")
    data = service.storage().read(row.object_key)
    import hashlib
    if len(data) != row.bytes or hashlib.sha256(data).hexdigest() != row.sha256:
        raise AppError(503, "attachment_unavailable", "附件暂不可用")
    return Response(data, media_type=row.mime, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Disposition": "attachment; filename=attachment; filename*=UTF-8''" + quote(row.filename, safe="")})
