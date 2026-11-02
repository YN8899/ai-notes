import hashlib
import io
import json
import re
import uuid
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from PIL import Image, UnidentifiedImageError
from app.shared.db import utcnow
from app.shared.errors import AppError
from app.shared.models import User
from app.modules.files.service import storage
from app.modules.social import service
from app.modules.social.chat_models import ChatAttachment, ChatMessage, ChatReadCursor, ChatThread


def thread(db, user, thread_id, *, lock=False):
    stmt = select(ChatThread).where(ChatThread.id == thread_id, or_(ChatThread.user_low_id == user.id, ChatThread.user_high_id == user.id))
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if not row:
        raise AppError(404, "chat_not_found", "对话不存在")
    return row


def peer_id(row, user_id):
    return row.user_high_id if row.user_low_id == user_id else row.user_low_id


def can_send(db, row, user):
    peer = db.get(User, peer_id(row, user.id))
    return bool(peer and peer.status == "active" and service.are_friends(db, user.id, peer.id))


def require_send(db, row, user):
    # Serialize send/upload against unfriend, which revokes capabilities under
    # this same canonical relationship lock. Thread is always locked first.
    service.relationship_row(db, user.id, peer_id(row, user.id), lock=True)
    if not can_send(db, row, user):
        raise AppError(403, "friend_required", "只有当前好友可以发送消息")


def thread_output(db, row, user):
    peer = db.get(User, peer_id(row, user.id))
    cursors = dict(db.execute(select(ChatReadCursor.user_id, ChatReadCursor.last_read_seq).where(ChatReadCursor.thread_id == row.id)).all())
    read = cursors.get(user.id, 0)
    unread = db.scalar(select(func.count()).select_from(ChatMessage).where(ChatMessage.thread_id == row.id, ChatMessage.sender_id != user.id, ChatMessage.seq > read))
    return dict(id=row.id, peer_id=peer.id, peer_name=peer.display_name if peer.status == "active" else "已停用成员", peer_username=peer.username if peer.status == "active" else "", can_send=can_send(db, row, user), last_seq=row.last_seq, last_read_seq=read, peer_read_seq=cursors.get(peer.id, 0), unread=unread, created_at=row.created_at)


def create_thread(db, user, target_id):
    service.active_user(db, target_id)
    if target_id == user.id or not service.are_friends(db, user.id, target_id):
        raise AppError(403, "friend_required", "请先添加对方为好友")
    low, high = sorted([user.id, target_id])
    db.execute(insert(ChatThread).values(id=uuid.uuid4(), created_at=utcnow(), user_low_id=low, user_high_id=high, last_seq=0).on_conflict_do_nothing(index_elements=[ChatThread.user_low_id, ChatThread.user_high_id]))
    return db.scalar(select(ChatThread).where(ChatThread.user_low_id == low, ChatThread.user_high_id == high))


def message_output(db, row):
    attachments = db.scalars(select(ChatAttachment).where(ChatAttachment.message_id == row.id).order_by(ChatAttachment.created_at, ChatAttachment.id)).all()
    return dict(id=row.id, thread_id=row.thread_id, sender_id=row.sender_id, client_message_id=row.client_message_id, seq=row.seq, body=row.body, created_at=row.created_at, attachments=attachments)


def send(db, user, thread_id, data):
    row = thread(db, user, thread_id, lock=True)
    require_send(db, row, user)
    fingerprint = hashlib.sha256(json.dumps(dict(thread=str(row.id), body=data.body, attachments=[str(i) for i in data.attachment_ids]), sort_keys=True).encode()).hexdigest()
    previous = db.scalar(select(ChatMessage).where(ChatMessage.sender_id == user.id, ChatMessage.client_message_id == data.client_message_id))
    if previous:
        if previous.thread_id != row.id or previous.fingerprint != fingerprint:
            raise AppError(409, "message_key_conflict", "该发送编号已用于其他内容，请重新发送")
        return previous
    attachments = []
    for attachment_id in data.attachment_ids:
        attachment = db.scalar(select(ChatAttachment).where(ChatAttachment.id == attachment_id, ChatAttachment.owner_id == user.id, ChatAttachment.thread_id == row.id).with_for_update())
        if not attachment or attachment.message_id is not None:
            raise AppError(409, "attachment_unavailable", "附件不存在、属于其他对话或已发送")
        attachments.append(attachment)
    row.last_seq += 1
    message = ChatMessage(thread_id=row.id, sender_id=user.id, client_message_id=data.client_message_id, seq=row.last_seq, body=data.body, fingerprint=fingerprint)
    db.add(message)
    db.flush()
    for attachment in attachments:
        attachment.message_id = message.id
    db.flush()
    from app.modules.social.realtime_signal import publish
    for owner_id in (row.user_low_id, row.user_high_id):
        publish(db, owner_id, "chat")
    return message


def validate_attachment(data, filename):
    if not data or len(data) > 10 * 1024 * 1024:
        raise AppError(413, "attachment_size", "附件需为1字节至10 MiB")
    filename = filename.strip()
    if not filename or len(filename) > 200 or any(ord(c) < 32 for c in filename) or "/" in filename or "\\" in filename:
        raise AppError(422, "attachment_name", "附件文件名不合法")
    suffix = filename.rsplit(".", 1)[-1].lower()
    if suffix in {"png", "jpg", "jpeg", "webp"}:
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"} or image.width * image.height > 20_000_000:
                    raise ValueError("image_size")
                image.verify()
                mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[image.format]
            # verify() alone accepts truncated JPEG pixel streams. Decode the
            # bounded image fully before accepting any attachment bytes.
            with Image.open(io.BytesIO(data)) as image:
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("animated_image")
                image.load()
            return mime
        except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
            raise AppError(422, "attachment_invalid", "图片无法解码或尺寸过大")
    if suffix == "pdf":
        import pypdfium2 as pdfium
        try:
            doc = pdfium.PdfDocument(data)
            try:
                if not 1 <= len(doc) <= 200:
                    raise ValueError("pdf_pages")
            finally:
                doc.close()
        except Exception:
            raise AppError(422, "attachment_invalid", "PDF无法读取或超过200页")
        return "application/pdf"
    if suffix in {"txt", "md"}:
        try:
            text = data.decode("utf-8")
            if "\x00" in text:
                raise ValueError("binary_text")
        except (UnicodeError, ValueError):
            raise AppError(422, "attachment_invalid", "文本附件需为UTF-8格式")
        return "text/plain"
    raise AppError(422, "attachment_type", "支持PNG/JPEG/WebP/PDF/TXT/MD附件")


def upload_attachment(db, user, thread_id, data, filename, request_key):
    row = thread(db, user, thread_id, lock=True)
    require_send(db, row, user)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_key):
        raise AppError(422, "attachment_request_key", "附件请求编号不合法")
    mime = validate_attachment(data, filename)
    checksum = hashlib.sha256(data).hexdigest()
    previous = db.scalar(select(ChatAttachment).where(ChatAttachment.owner_id == user.id, ChatAttachment.request_key == request_key))
    if previous:
        if previous.thread_id != row.id or previous.filename != filename or previous.sha256 != checksum:
            raise AppError(409, "attachment_key_conflict", "附件编号已用于其他内容")
        return previous
    # Limit unattached uploads as well as total retained bytes, preventing abandoned upload abuse.
    count, total = db.execute(select(func.count(), func.coalesce(func.sum(ChatAttachment.bytes), 0)).where(ChatAttachment.owner_id == user.id)).one()
    pending = db.scalar(select(func.count()).select_from(ChatAttachment).where(ChatAttachment.owner_id == user.id, ChatAttachment.message_id.is_(None)))
    if total + len(data) > 512 * 1024 * 1024 or pending >= 50:
        raise AppError(409, "attachment_quota", "聊天附件容量或待发送数量已达上限")
    attachment_id = uuid.uuid4()
    key = f"chat/{user.id}/{attachment_id}/original"
    storage().put(key, data)
    attachment = ChatAttachment(id=attachment_id, thread_id=row.id, owner_id=user.id, object_key=key, filename=filename, mime=mime, bytes=len(data), sha256=checksum, request_key=request_key)
    db.add(attachment)
    db.flush()
    return attachment
