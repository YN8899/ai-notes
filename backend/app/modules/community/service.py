"""Published views are frozen content, never an authorization to private notes."""
import hashlib
import json
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import and_, case, delete, exists, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert

from app.modules.community import schemas as s
from app.modules.community.models import CommunityAsset, CommunityComment, CommunityPost, CommunityReaction, CommunityReport, CommunitySnapshot
from app.modules.files.models import File, FileVariant, NotePage, PdfImport
from app.modules.files.oss_storage import StorageUnavailable
from app.modules.files.service import storage
from app.modules.notes import cursors
from app.modules.notes.models import Note
from app.modules.notes.service import owned, revision_matches
from app.modules.social.models import Follow
from app.modules.social.service import active_user, pagination, page_result
from app.shared.db import utcnow
from app.shared.errors import AppError
from app.shared.models import Audit, Job, User


def not_found():
    return AppError(404, "not_found", "帖子不存在或无权访问")


def author_output(user):
    return {"id": user.id, "username": user.username, "display_name": user.display_name}


def public_document(body):
    """Allowlisted Tiptap data drops private image URLs and arbitrary metadata."""
    if not body:
        return {}
    allowed = {"doc", "paragraph", "text", "heading", "bulletList", "orderedList", "listItem", "blockquote", "codeBlock", "hardBreak", "horizontalRule", "inlineMath", "blockMath", "mathematics", "mathInline", "mathBlock"}

    def clean(node):
        if not isinstance(node, dict) or node.get("type") not in allowed:
            return None
        result = {"type": node["type"]}
        if node["type"] == "text" and isinstance(node.get("text"), str):
            result["text"] = node["text"]
        attrs = node.get("attrs", {})
        if isinstance(attrs, dict):
            if node["type"] == "heading" and type(attrs.get("level")) is int and 1 <= attrs["level"] <= 6:
                result["attrs"] = {"level": attrs["level"]}
            elif node["type"] == "orderedList" and type(attrs.get("start")) is int and 1 <= attrs["start"] <= 10000:
                result["attrs"] = {"start": attrs["start"]}
            elif node["type"] == "codeBlock" and isinstance(attrs.get("language"), str):
                result["attrs"] = {"language": attrs["language"][:32]}
            elif "Math" in node["type"] or "math" in node["type"]:
                latex = attrs.get("latex", "")
                if isinstance(latex, str):
                    result["attrs"] = {"latex": latex[:8192]}
        if isinstance(node.get("content"), list):
            result["content"] = [item for child in node["content"] if (item := clean(child)) is not None]
        marks = []
        for mark in node.get("marks", []) if isinstance(node.get("marks"), list) else []:
            if not isinstance(mark, dict):
                continue
            if mark.get("type") in {"bold", "italic", "strike", "code", "underline"}:
                marks.append({"type": mark["type"]})
            elif mark.get("type") == "link":
                href = mark.get("attrs", {}).get("href") if isinstance(mark.get("attrs"), dict) else None
                if isinstance(href, str) and not any(ord(c) < 33 for c in href):
                    try:
                        url = urlsplit(href)
                        if url.scheme in {"http", "https"} and url.hostname and not url.path.startswith("/api/"):
                            marks.append({"type": "link", "attrs": {"href": href, "target": "_blank", "rel": "noopener noreferrer"}})
                    except ValueError:
                        pass
        if marks:
            result["marks"] = marks
        return result

    return clean(body) or {}


def get_owned(db, user, post_id, *, lock=False):
    stmt = select(CommunityPost).where(CommunityPost.id == post_id, CommunityPost.owner_id == user.id)
    post = db.scalar(stmt.with_for_update() if lock else stmt)
    if post is None:
        raise not_found()
    return post


def public_post(db, post_id, *, lock=False):
    stmt = select(CommunityPost).join(User, User.id == CommunityPost.owner_id).where(
        CommunityPost.id == post_id, CommunityPost.status == "published", User.status == "active",
        CommunityPost.live_snapshot_id.is_not(None))
    post = db.scalar(stmt.with_for_update(of=CommunityPost) if lock else stmt)
    if post is None:
        raise not_found()
    snapshot = db.get(CommunitySnapshot, post.live_snapshot_id)
    if snapshot is None or snapshot.post_id != post.id or snapshot.owner_id != post.owner_id or snapshot.status != "ready":
        raise not_found()
    return post, snapshot


def frozen_source(db, post, note_id, revision):
    if not post or not post.live_snapshot_id or note_id is None:
        return None
    snapshot = db.get(CommunitySnapshot, post.live_snapshot_id)
    if snapshot and snapshot.status == "ready" and snapshot.post_id == post.id and snapshot.owner_id == post.owner_id and snapshot.source_note_id == note_id and snapshot.source_revision == revision:
        return snapshot
    return None


def source_note(db, user, data, post=None):
    if data.source_note_id is None:
        return None
    if frozen_source(db, post, data.source_note_id, data.source_revision):
        return None
    note = owned(db, user, Note, data.source_note_id)
    revision_matches(note, data.source_revision)
    return note


def create_post(db, user, data):
    note = source_note(db, user, data)
    values = data.model_dump()
    if note and not data.body_json and not data.plain_text:
        values.update(body_json=public_document(note.body_json), plain_text=note.plain_text)
    else:
        values["body_json"] = public_document(data.body_json)
    post = CommunityPost(owner_id=user.id, **values)
    db.add(post)
    db.flush()
    return draft_output(db, post)


def update_post(db, user, post_id, data):
    post = get_owned(db, user, post_id, lock=True)
    revision_matches(post, data.revision)
    preparing = db.get(CommunitySnapshot, post.preparing_snapshot_id) if post.preparing_snapshot_id else None
    if preparing and preparing.status == "preparing":
        raise AppError(409, "publish_in_progress", "正在准备发布，请等待完成后编辑")
    note = source_note(db, user, data, post)
    values = data.model_dump(exclude={"revision"})
    if note and not data.body_json and not data.plain_text:
        values.update(body_json=public_document(note.body_json), plain_text=note.plain_text)
    else:
        values["body_json"] = public_document(data.body_json)
    for name, value in values.items():
        setattr(post, name, value)
    post.revision += 1
    post.updated_at = utcnow()
    db.flush()
    return draft_output(db, post)


def draft_output(db, post):
    snapshot = db.get(CommunitySnapshot, post.preparing_snapshot_id) if post.preparing_snapshot_id else None
    job = db.get(Job, snapshot.job_id) if snapshot and snapshot.job_id else None
    failed = snapshot and snapshot.status == "preparing" and job and job.status == "failed"
    return {key: getattr(post, key) for key in s.CommunityDraftOut.model_fields if key not in {"publish_status", "publish_error"}} | {
        "publish_status": "failed" if failed else snapshot.status if snapshot else None,
        "publish_error": job.error_code if failed else snapshot.error_code if snapshot else None}


def list_drafts(db, user, *, cursor=None, limit=20):
    scope = cursors.scope_key("community-drafts", user.id)
    stmt, cutoff = pagination(select(CommunityPost).where(CommunityPost.owner_id == user.id), CommunityPost,
        scope=scope, cursor=cursor, limit=limit)
    return page_result(db.scalars(stmt).all(), cutoff, scope, limit, lambda row: draft_output(db, row))


def publish_output(snapshot):
    return {"post_id": snapshot.post_id, "snapshot_id": snapshot.id, "job_id": snapshot.job_id, "status": snapshot.status}


def publish_post(db, user, post_id, data):
    post = get_owned(db, user, post_id, lock=True)
    request_hash = hashlib.sha256(json.dumps({"post_id": str(post_id), "revision": data.revision}, sort_keys=True).encode()).hexdigest()
    old = db.scalar(select(CommunitySnapshot).where(CommunitySnapshot.owner_id == user.id, CommunitySnapshot.request_key == data.request_key))
    if old:
        if old.request_hash != request_hash:
            raise AppError(409, "request_key_conflict", "同一个发布标识不能用于不同内容")
        return publish_output(old)
    revision_matches(post, data.revision)
    if post.status == "hidden":
        raise AppError(409, "post_hidden", "帖子已被下架，不能重新发布绕过审核")
    pending = db.get(CommunitySnapshot, post.preparing_snapshot_id) if post.preparing_snapshot_id else None
    if pending and pending.status == "preparing":
        raise AppError(409, "publish_in_progress", "已有发布任务正在准备")
    if post.source_note_id and not frozen_source(db, post, post.source_note_id, post.source_revision):
        note = owned(db, user, Note, post.source_note_id)
        revision_matches(note, post.source_revision)
    version = (db.scalar(select(func.max(CommunitySnapshot.version)).where(CommunitySnapshot.post_id == post.id)) or 0) + 1
    snapshot = CommunitySnapshot(owner_id=user.id, post_id=post.id, version=version,
        title=post.title, body_json=public_document(post.body_json), plain_text=post.plain_text,
        subject=post.subject, post_type=post.post_type, tags=post.tags[:], source_note_id=post.source_note_id, source_revision=post.source_revision,
        request_key=data.request_key, request_hash=request_hash)
    db.add(snapshot)
    db.flush()
    assets = freeze_assets(db, user, post, snapshot)
    snapshot.recommendation_score = min(3, int(len(snapshot.plain_text.strip()) >= 200) + (2 if assets else 0))
    job = Job(owner_id=user.id, kind="community.publish", dedupe_key="community_" + str(data.request_key),
        payload={"post_id": str(post.id), "snapshot_id": str(snapshot.id)}, max_attempts=3)
    db.add(job)
    db.flush()
    snapshot.job_id = job.id
    post.preparing_snapshot_id = snapshot.id
    post.revision += 1
    post.updated_at = utcnow()
    db.flush()
    return publish_output(snapshot)


def freeze_assets(db, user, post, snapshot):
    if post.source_note_id is None:
        return []
    previous = frozen_source(db, post, post.source_note_id, post.source_revision)
    if previous:
        # Publish edits from the independent community copy, even after the
        # private note/pages/files were changed or removed. A newly selected
        # private source still follows the explicit readiness checks below.
        originals = db.scalars(select(CommunityAsset).where(CommunityAsset.snapshot_id == previous.id, CommunityAsset.owner_id == user.id).order_by(CommunityAsset.position)).all()
        if any(row.status != "ready" or not row.sha256 for row in originals):
            raise AppError(409, "source_not_ready", "上个社区副本尚未完整准备")
        assets = []
        for original in originals:
            identity = uuid4()
            prefix = f"community/{snapshot.id}/{identity}"
            row = CommunityAsset(id=identity, owner_id=user.id, snapshot_id=snapshot.id, position=original.position,
                filename=original.filename, mime=original.mime, width=original.width, height=original.height,
                object_key=prefix+"/reading.jpg", original_object_key=prefix+"/original",
                source_object_key=original.object_key, source_original_object_key=original.original_object_key,
                bytes=original.bytes, original_bytes=original.original_bytes,
                sha256=original.sha256, original_sha256=original.original_sha256)
            db.add(row)
            assets.append(row)
        db.flush()
        return assets
    imported = db.scalar(select(PdfImport).where(PdfImport.note_id == post.source_note_id, PdfImport.owner_id == user.id))
    if imported and (imported.status != "ready" or not imported.page_count or imported.completed_pages != imported.page_count):
        raise AppError(409, "source_not_ready", "PDF完整转换成功后才能发布整份副本")
    rows = db.execute(select(NotePage, File, FileVariant).join(File, and_(File.id == NotePage.file_id, File.owner_id == NotePage.owner_id))
        .join(FileVariant, and_(FileVariant.file_id == File.id, FileVariant.kind == "reading"))
        .where(NotePage.note_id == post.source_note_id, NotePage.owner_id == user.id,
               NotePage.deleted_at.is_(None)).order_by(NotePage.position, NotePage.id)).all()
    page_count = db.scalar(select(func.count()).select_from(NotePage).where(NotePage.note_id == post.source_note_id,
                           NotePage.owner_id == user.id, NotePage.deleted_at.is_(None)))
    if len(rows) != page_count or any(file.status != "ready" or not file.sha256 or not file.bytes for _, file, _ in rows):
        raise AppError(409, "source_not_ready", "原笔记的全部页面准备完成后才能发布")
    if len(rows) > 200 or sum(file.bytes + variant.bytes for _, file, variant in rows) > 100 * 1024 * 1024:
        raise AppError(413, "publication_too_large", "单次发布最多200页、100MiB原图与阅读图")
    assets = []
    for position, (_, file, variant) in enumerate(rows):
        try:
            reader = storage()
            if reader.head(variant.object_key).size != variant.bytes:
                raise ValueError("source_size")
            reading = reader.read(variant.object_key)
            if len(reading) != variant.bytes:
                raise ValueError("source_size")
        except (OSError, StorageUnavailable, ValueError):
            raise AppError(409, "source_unavailable", "来源阅读图暂不可用，请核对原笔记后重试") from None
        asset_id = uuid4()
        prefix = f"community/{snapshot.id}/{asset_id}"
        asset = CommunityAsset(id=asset_id, owner_id=user.id, snapshot_id=snapshot.id, position=position,
            filename=file.filename, mime=file.mime, width=file.width, height=file.height,
            object_key=prefix + "/reading.jpg", original_object_key=prefix + "/original",
            source_object_key=variant.object_key, source_original_object_key=file.object_key,
            bytes=variant.bytes, original_bytes=file.bytes, sha256=hashlib.sha256(reading).hexdigest(),
            original_sha256=file.sha256)
        db.add(asset)
        assets.append(asset)
    db.flush()
    return assets


def retry_publish(db, user, post_id, revision):
    post = get_owned(db, user, post_id, lock=True)
    revision_matches(post, revision)
    snapshot = db.get(CommunitySnapshot, post.preparing_snapshot_id) if post.preparing_snapshot_id else None
    if post.status == "hidden" or not snapshot or not snapshot.job_id:
        raise AppError(409, "retry_unavailable", "没有可重试的发布任务")
    job = db.scalar(select(Job).where(Job.id == snapshot.job_id).with_for_update())
    if not job or job.status != "failed" or snapshot.status not in {"preparing", "failed"}:
        raise AppError(409, "retry_unavailable", "任务未失败或已经结束")
    job.status, job.attempts, job.run_after = "queued", 0, utcnow()
    job.lease_until, job.worker_token, job.error_code = None, None, None
    snapshot.status, snapshot.error_code = "preparing", None
    post.revision += 1
    post.updated_at = utcnow()
    return publish_output(snapshot)


def withdraw_post(db, user, post_id, revision):
    post = get_owned(db, user, post_id, lock=True)
    revision_matches(post, revision)
    if post.status == "hidden":
        raise AppError(409, "post_hidden", "已下架帖子请联系管理员，不能通过撤回绕过审核")
    snapshot = db.get(CommunitySnapshot, post.preparing_snapshot_id) if post.preparing_snapshot_id else None
    if snapshot and snapshot.status == "preparing":
        snapshot.status, snapshot.error_code = "cancelled", "withdrawn"
        if snapshot.job_id:
            job = db.scalar(select(Job).where(Job.id == snapshot.job_id).with_for_update())
            if job and job.status in {"queued", "running"}:
                job.status, job.error_code = "failed", "publication_cancelled"
                job.worker_token, job.lease_until = None, None
    post.status = "withdrawn"
    post.revision += 1
    post.updated_at = utcnow()
    db.flush()
    return draft_output(db, post)


def reaction_count(db, post_id, kind):
    return db.scalar(select(func.count()).select_from(CommunityReaction).join(User, User.id == CommunityReaction.owner_id)
        .where(CommunityReaction.post_id == post_id, CommunityReaction.kind == kind, User.status == "active"))


def summary(db, viewer, post, snapshot=None):
    snapshot = snapshot or db.get(CommunitySnapshot, post.live_snapshot_id)
    author = active_user(db, post.owner_id)
    reactions = set(db.scalars(select(CommunityReaction.kind).where(CommunityReaction.post_id == post.id, CommunityReaction.owner_id == viewer.id)))
    comments = db.scalar(select(func.count()).select_from(CommunityComment).join(User, User.id == CommunityComment.owner_id)
        .where(CommunityComment.post_id == post.id, CommunityComment.deleted_at.is_(None), User.status == "active"))
    reason = "按发布时间展示"
    if snapshot.recommendation_score:
        reason = "内容完整度规则：" + "、".join((["含独立图片"] if snapshot.recommendation_score >= 2 else []) + (["正文至少200字"] if snapshot.recommendation_score % 2 else [])) + "；同分按发布时间"
    return {"id": post.id, "author": author_output(author), "title": snapshot.title, "subject": snapshot.subject,
        "post_type": snapshot.post_type, "tags": snapshot.tags, "excerpt": snapshot.plain_text[:240],
        "published_at": post.published_at, "snapshot_version": snapshot.version,
        "like_count": reaction_count(db, post.id, "like"), "comment_count": comments,
        "liked": "like" in reactions, "bookmarked": "bookmark" in reactions, "recommendation_reason": reason}


def asset_output(post_id, asset):
    url = f"/api/v1/community/posts/{post_id}/assets/{asset.id}"
    return {"id": asset.id, "position": asset.position, "filename": asset.filename, "width": asset.width,
        "height": asset.height, "image_url": url, "original_url": url + "?kind=original"}


def read_post(db, viewer, post_id):
    post, snapshot = public_post(db, post_id)
    assets = db.scalars(select(CommunityAsset).where(CommunityAsset.snapshot_id == snapshot.id, CommunityAsset.status == "ready").order_by(CommunityAsset.position)).all()
    return {**summary(db, viewer, post, snapshot), "body_json": snapshot.body_json, "plain_text": snapshot.plain_text,
        "assets": [asset_output(post.id, row) for row in assets], "source_revision": snapshot.source_revision}


def feed(db, viewer, *, mode="latest", subject=None, post_type=None, tag=None, cursor=None, limit=20, bookmarked=False):
    scope = cursors.scope_key("community-feed", viewer.id, mode, subject, post_type, tag, bookmarked)
    cutoff, anchor = utcnow(), None
    if cursor:
        data = cursors.decode_cursor(cursor, {"v", "scope", "cutoff", "at", "id", "score"}, scope)
        # Reuse canonical timestamp/UUID validation after removing the rank.
        cutoff, at, post_id = cursors.note_position(cursors.encode_cursor({k: v for k, v in data.items() if k != "score"}), scope)
        if type(data["score"]) is not int or not 0 <= data["score"] <= 3:
            raise cursors.invalid_cursor()
        anchor = (data["score"], at, post_id)
    score = CommunitySnapshot.recommendation_score if mode == "recommended" else case((CommunityPost.id.is_not(None), 0), else_=0)
    stmt = select(CommunityPost, CommunitySnapshot).join(CommunitySnapshot, and_(CommunitySnapshot.id == CommunityPost.live_snapshot_id,
        CommunitySnapshot.post_id == CommunityPost.id, CommunitySnapshot.owner_id == CommunityPost.owner_id))
    stmt = stmt.join(User, User.id == CommunityPost.owner_id).where(CommunityPost.status == "published", User.status == "active",
        CommunitySnapshot.status == "ready", CommunityPost.published_at <= cutoff)
    if mode == "following":
        stmt = stmt.where(CommunityPost.owner_id.in_(select(Follow.followee_id).where(Follow.follower_id == viewer.id)))
    if bookmarked:
        stmt = stmt.where(CommunityPost.id.in_(select(CommunityReaction.post_id).where(CommunityReaction.owner_id == viewer.id, CommunityReaction.kind == "bookmark")))
    if subject is not None:
        stmt = stmt.where(CommunitySnapshot.subject == subject)
    if post_type is not None:
        stmt = stmt.where(CommunitySnapshot.post_type == post_type)
    if tag is not None:
        stmt = stmt.where(CommunitySnapshot.tags.contains([tag]))
    if anchor:
        stmt = stmt.where(tuple_(score, CommunityPost.published_at, CommunityPost.id) < anchor)
    rows = db.execute(stmt.order_by(score.desc(), CommunityPost.published_at.desc(), CommunityPost.id.desc()).limit(limit + 1)).all()
    more, rows = len(rows) > limit, rows[:limit]
    next_cursor = None
    if more and rows:
        last, snap = rows[-1]
        next_cursor = cursors.encode_cursor({"v": 1, "scope": scope, "cutoff": cutoff.isoformat(), "at": last.published_at.isoformat(),
            "id": str(last.id), "score": snap.recommendation_score if mode == "recommended" else 0})
    return {"items": [summary(db, viewer, post, snapshot) for post, snapshot in rows], "next_cursor": next_cursor, "limit": limit}


def read_asset(db, viewer, post_id, asset_id, kind):
    post, snapshot = public_post(db, post_id)
    asset = db.scalar(select(CommunityAsset).where(CommunityAsset.id == asset_id,
        CommunityAsset.snapshot_id == snapshot.id, CommunityAsset.owner_id == post.owner_id, CommunityAsset.status == "ready"))
    if asset is None:
        raise not_found()
    try:
        data = storage().read(asset.object_key if kind == "reading" else asset.original_object_key)
    except (OSError, StorageUnavailable):
        raise AppError(503, "file_unavailable", "社区副本暂不可用") from None
    expected = asset.sha256 if kind == "reading" else asset.original_sha256
    if len(data) != (asset.bytes if kind == "reading" else asset.original_bytes) or hashlib.sha256(data).hexdigest() != expected:
        raise AppError(503, "file_unavailable", "社区副本完整性检查失败")
    return data, "image/jpeg" if kind == "reading" else asset.mime, asset.filename


def set_reaction(db, viewer, post_id, kind, enabled):
    public_post(db, post_id, lock=True)
    if enabled:
        db.execute(insert(CommunityReaction).values(id=uuid4(), created_at=utcnow(), owner_id=viewer.id, post_id=post_id, kind=kind)
            .on_conflict_do_nothing(constraint="uq_community_reaction"))
    else:
        db.execute(delete(CommunityReaction).where(CommunityReaction.owner_id == viewer.id, CommunityReaction.post_id == post_id, CommunityReaction.kind == kind))


def comment_output(db, comment):
    return {"id": comment.id, "post_id": comment.post_id, "author": author_output(active_user(db, comment.owner_id)),
        "body": comment.body, "created_at": comment.created_at}


def create_comment(db, viewer, post_id, data):
    public_post(db, post_id, lock=True)
    old = db.scalar(select(CommunityComment).where(CommunityComment.owner_id == viewer.id, CommunityComment.request_key == data.request_key))
    if old:
        if old.post_id != post_id or old.body != data.body or old.deleted_at is not None:
            raise AppError(409, "request_key_conflict", "同一个评论标识不能用于不同内容")
        return comment_output(db, old)
    row = CommunityComment(owner_id=viewer.id, post_id=post_id, body=data.body, request_key=data.request_key)
    db.add(row)
    db.flush()
    return comment_output(db, row)


def list_comments(db, viewer, post_id, *, cursor=None, limit=30):
    public_post(db, post_id)
    scope = cursors.scope_key("community-comments", viewer.id, post_id)
    stmt = select(CommunityComment).join(User, User.id == CommunityComment.owner_id).where(
        CommunityComment.post_id == post_id, CommunityComment.deleted_at.is_(None), User.status == "active")
    stmt, cutoff = pagination(stmt, CommunityComment, scope=scope, cursor=cursor, limit=limit)
    return page_result(db.scalars(stmt).all(), cutoff, scope, limit, lambda row: comment_output(db, row))


def delete_comment(db, viewer, comment_id):
    row = db.scalar(select(CommunityComment).where(CommunityComment.id == comment_id, CommunityComment.owner_id == viewer.id).with_for_update())
    if row is None:
        raise not_found()
    # Author may delete their own history even when the parent was withdrawn.
    row.deleted_at = row.deleted_at or utcnow()


def report_output(row):
    return {key: getattr(row, key) for key in s.CommunityReportOut.model_fields}


def create_report(db, viewer, post_id, data):
    public_post(db, post_id, lock=True)
    old = db.scalar(select(CommunityReport).where(CommunityReport.owner_id == viewer.id, CommunityReport.request_key == data.request_key))
    if old:
        if old.post_id != post_id or old.reason != data.reason:
            raise AppError(409, "request_key_conflict", "同一个举报标识不能用于不同内容")
        return report_output(old)
    row = CommunityReport(owner_id=viewer.id, post_id=post_id, reason=data.reason, request_key=data.request_key)
    db.add(row)
    db.flush()
    return report_output(row)


def list_reports(db, admin, *, status="pending", cursor=None, limit=20):
    scope = cursors.scope_key("community-reports", admin.id, status)
    stmt = select(CommunityReport).where(CommunityReport.status == status)
    stmt, cutoff = pagination(stmt, CommunityReport, scope=scope, cursor=cursor, limit=limit)
    return page_result(db.scalars(stmt).all(), cutoff, scope, limit, report_output)


def moderate_post(db, admin, post_id, decision, reason, request_id):
    post = db.scalar(select(CommunityPost).where(CommunityPost.id == post_id).with_for_update())
    if post is None or (decision == "hide" and post.status not in {"published", "hidden"}) or (decision == "restore" and post.status != "hidden"):
        raise not_found()
    # Moderators cannot browse drafts, source notes or private owner data.
    if post.live_snapshot_id is None:
        raise not_found()
    post.status = "hidden" if decision == "hide" else "published"
    post.moderation_reason = reason if decision == "hide" else None
    post.revision += 1
    post.updated_at = utcnow()
    pending = db.get(CommunitySnapshot, post.preparing_snapshot_id) if post.preparing_snapshot_id else None
    if decision == "hide" and pending and pending.status == "preparing":
        pending.status, pending.error_code = "cancelled", "moderated"
        if pending.job_id:
            job = db.scalar(select(Job).where(Job.id == pending.job_id).with_for_update())
            if job and job.status in {"queued", "running"}:
                job.status, job.error_code = "failed", "publication_cancelled"
                job.worker_token, job.lease_until = None, None
    db.add(Audit(actor_id=admin.id, action="community." + decision, resource_id=str(post.id), request_id=request_id,
        details={"reason": reason}))


def resolve_report(db, admin, report_id, data, request_id):
    # Resolve without holding a report lock before the post lock: moderation and
    # owner operations consistently lock the post first.
    row = db.get(CommunityReport, report_id)
    if row is None:
        raise not_found()
    post_id = row.post_id
    post = db.scalar(select(CommunityPost).where(CommunityPost.id == post_id).with_for_update())
    row = db.scalar(select(CommunityReport).where(CommunityReport.id == report_id).with_for_update())
    if row.status != "pending":
        raise AppError(409, "report_resolved", "举报已处理")
    if data.decision == "hide":
        moderate_post(db, admin, post_id, "hide", data.reason, request_id)
    else:
        db.add(Audit(actor_id=admin.id, action="community.report.dismiss", resource_id=str(post_id), request_id=request_id,
            details={"reason": data.reason, "report_id": str(row.id)}))
    row.status = "hidden" if data.decision == "hide" else "dismissed"
    row.resolved_by, row.resolved_at, row.resolution = admin.id, utcnow(), data.reason
    db.flush()
    return report_output(row)
