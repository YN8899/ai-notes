"""Private recovered history is readable, never a live social capability."""
import hashlib
from datetime import datetime
from typing import Literal
from urllib.parse import quote
from uuid import UUID
from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from app.shared.dependencies import DB, CurrentUser
from app.shared.errors import AppError
from app.modules.exports.social_models import SocialArchiveAsset, SocialArchiveRecord
from app.modules.files.service import storage
from app.modules.files.oss_storage import StorageUnavailable

router = APIRouter(prefix="/social/archive", tags=["recovered-social-history"])
Kind = Literal["profile", "friendship", "follow", "note_grant", "chat_thread", "chat_message", "chat_attachment", "community_post", "community_snapshot", "community_comment", "community_reaction", "community_report"]


class HistorySummary(BaseModel):
    id: UUID
    kind: Kind
    created_at: datetime
    title: str


class HistoryPage(BaseModel):
    items: list[HistorySummary]
    next_cursor: UUID | None


class HistoryAsset(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    filename: str
    mime: str
    bytes: int
    role: str


class HistoryDetail(HistorySummary):
    payload: dict
    assets: list[HistoryAsset]


def history_title(row):
    value = row.payload.get("title") or row.payload.get("body") or row.payload.get("filename") or row.payload.get("display_name")
    peer = row.payload.get("peer") or row.payload.get("sender")
    if not value and isinstance(peer, dict):
        value = peer.get("display_name")
    labels = {"profile":"个人资料", "friendship":"好友历史", "follow":"关注历史", "note_grant":"分享记录", "chat_thread":"会话历史", "chat_message":"聊天消息", "chat_attachment":"聊天附件", "community_post":"社区创作", "community_snapshot":"发布副本", "community_comment":"社区评论", "community_reaction":"点赞与收藏", "community_report":"举报记录"}
    return value[:200] if isinstance(value, str) and value.strip() else labels.get(row.kind, "恢复历史")


def owned_record(db, user, record_id):
    row = db.scalar(select(SocialArchiveRecord).where(SocialArchiveRecord.id == record_id, SocialArchiveRecord.owner_id == user.id))
    if row is None:
        raise AppError(404, "history_not_found", "恢复历史不存在或无权访问")
    return row


@router.get("/records", response_model=HistoryPage)
def records(db: DB, user: CurrentUser, kind: Kind | None = None, cursor: UUID | None = None, limit: int = Query(30, ge=1, le=100)):
    stmt = select(SocialArchiveRecord).where(SocialArchiveRecord.owner_id == user.id)
    if kind:
        stmt = stmt.where(SocialArchiveRecord.kind == kind)
    if cursor:
        stmt = stmt.where(SocialArchiveRecord.id > cursor)
    rows = db.scalars(stmt.order_by(SocialArchiveRecord.id).limit(limit + 1)).all()
    return dict(items=[dict(id=row.id, kind=row.kind, created_at=row.created_at, title=history_title(row)) for row in rows[:limit]], next_cursor=rows[limit-1].id if len(rows) > limit else None)


@router.get("/records/{record_id}", response_model=HistoryDetail)
def detail(record_id: UUID, db: DB, user: CurrentUser):
    row = owned_record(db, user, record_id)
    assets = db.scalars(select(SocialArchiveAsset).where(SocialArchiveAsset.owner_id == user.id, SocialArchiveAsset.record_id == row.id).order_by(SocialArchiveAsset.created_at, SocialArchiveAsset.id)).all()
    return dict(id=row.id, kind=row.kind, created_at=row.created_at, title=history_title(row), payload=row.payload, assets=assets)


@router.get("/assets/{asset_id}/content", response_class=Response,
    responses={200: {"content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
def asset(asset_id: UUID, db: DB, user: CurrentUser):
    row = db.scalar(select(SocialArchiveAsset).where(SocialArchiveAsset.id == asset_id, SocialArchiveAsset.owner_id == user.id))
    if row is None:
        raise AppError(404, "history_not_found", "历史附件不存在或无权访问")
    owned_record(db, user, row.record_id)
    try:
        data = storage().read(row.object_key)
    except (OSError, StorageUnavailable):
        raise AppError(503, "history_asset_unavailable", "历史附件暂不可用") from None
    if len(data) != row.bytes or hashlib.sha256(data).hexdigest() != row.sha256:
        raise AppError(503, "history_asset_unavailable", "历史附件完整性检查失败")
    return Response(data, media_type=row.mime, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "Content-Disposition": "attachment; filename=history-file; filename*=UTF-8''"+quote(row.filename,safe="")})
