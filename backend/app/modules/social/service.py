"""Relationship permissions. Shared reads never widen the private notes API."""
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, exists, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert

from app.modules.files.models import File, FileVariant, NotePage
from app.modules.files.service import storage
from app.modules.files.oss_storage import StorageUnavailable
from app.modules.notes import cursors
from app.modules.notes.models import Note
from app.modules.notes.service import owned, revision_matches
from app.modules.social.models import Follow, Friendship, NoteGrant, SocialProfile
from app.shared.db import utcnow
from app.shared.errors import AppError
from app.shared.models import User


def not_found():
    return AppError(404, "not_found", "成员或内容不存在，或无权访问")


def pair(first_id, second_id):
    return tuple(sorted((first_id, second_id)))


def active_user(db, user_id):
    user = db.scalar(select(User).where(User.id == user_id, User.status == "active"))
    if user is None:
        raise not_found()
    return user


def relationship_row(db, first_id, second_id, *, lock=False):
    low, high = pair(first_id, second_id)
    stmt = select(Friendship).where(Friendship.user_low_id == low, Friendship.user_high_id == high)
    return db.scalar(stmt.with_for_update() if lock else stmt)


def are_friends(db, first_id, second_id):
    if first_id == second_id:
        return False
    low, high = pair(first_id, second_id)
    active = select(User.id).where(User.id.in_([low, high]), User.status == "active")
    return bool(db.scalar(select(exists().where(Friendship.user_low_id == low,
        Friendship.user_high_id == high, Friendship.status == "accepted")))) and (
        len(db.scalars(active).all()) == 2)


def member_output(db, viewer, member):
    relation = "self" if viewer.id == member.id else "none"
    if relation != "self":
        row = relationship_row(db, viewer.id, member.id)
        if row and row.status == "accepted":
            relation = "friend"
        elif row and row.status == "pending":
            relation = "outgoing" if row.requester_id == viewer.id else "incoming"
    following = bool(db.scalar(select(exists().where(Follow.follower_id == viewer.id, Follow.followee_id == member.id))))
    return {"id": member.id, "username": member.username, "display_name": member.display_name,
            "relationship": relation, "following": following}


def profile_output(db, viewer, target_id):
    target = active_user(db, target_id)
    profile = db.scalar(select(SocialProfile).where(SocialProfile.owner_id == target_id))
    is_self = viewer.id == target_id
    visible = is_self or ((profile is None or profile.visibility == "friends") and are_friends(db, viewer.id, target_id))
    return {**member_output(db, viewer, target), "details_visible": visible,
            "bio": (profile.bio if profile else "") if visible else None,
            "subject": (profile.subject if profile else "") if visible else None,
            "details": (profile.details if profile else "") if visible else None,
            "visibility": (profile.visibility if profile else "friends") if is_self else None,
            "revision": (profile.revision if profile else 1) if is_self else None}


def update_profile(db, user, data):
    display_name = data.display_name.strip()
    if not display_name:
        raise AppError(422, "display_name_invalid", "显示名不能为空")
    profile = db.scalar(select(SocialProfile).where(SocialProfile.owner_id == user.id).with_for_update())
    if profile:
        revision_matches(profile, data.revision)
    elif data.revision != 1:
        raise AppError(409, "revision_conflict", "资料已更新，请重新加载")
    if not profile:
        profile = SocialProfile(owner_id=user.id)
        db.add(profile)
    user.display_name = display_name
    for field in ("bio", "subject", "details", "visibility"):
        setattr(profile, field, getattr(data, field))
    profile.revision = data.revision + 1
    db.flush()
    return profile_output(db, user, user.id)


def pagination(stmt, model, *, scope, cursor, limit):
    cutoff, anchor = utcnow(), None
    if cursor:
        cutoff, at, item_id = cursors.note_position(cursor, scope)
        anchor = (at, item_id)
    stmt = stmt.where(model.created_at <= cutoff)
    if anchor:
        stmt = stmt.where(tuple_(model.created_at, model.id) < anchor)
    return stmt.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1), cutoff


def page_result(rows, cutoff, scope, limit, output):
    more = len(rows) > limit
    rows = rows[:limit]
    cursor = None
    if rows and more:
        last = rows[-1]
        cursor = cursors.encode_cursor({"v": 1, "scope": scope, "cutoff": cutoff.isoformat(),
            "at": last.created_at.isoformat(), "id": str(last.id)})
    return {"items": [output(row) for row in rows], "next_cursor": cursor, "limit": limit}


def list_members(db, viewer, *, q="", cursor=None, limit=20, following=False):
    scope = cursors.scope_key("social-members", viewer.id, q, following)
    stmt = select(User).where(User.status == "active", User.id != viewer.id)
    if q:
        # Search is deliberately restricted to handles rather than private fields.
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(User.username.ilike("%" + escaped + "%", escape="\\"))
    if following:
        stmt = stmt.where(User.id.in_(select(Follow.followee_id).where(Follow.follower_id == viewer.id)))
    stmt, cutoff = pagination(stmt, User, scope=scope, cursor=cursor, limit=limit)
    return page_result(db.scalars(stmt).all(), cutoff, scope, limit, lambda member: member_output(db, viewer, member))


def request_output(db, viewer, row):
    target_id = row.user_high_id if row.user_low_id == viewer.id else row.user_low_id
    return {"id": row.id, "member": member_output(db, viewer, active_user(db, target_id)),
        "requester_id": row.requester_id, "status": row.status, "revision": row.revision,
        "created_at": row.created_at, "updated_at": row.updated_at}


def request_friendship(db, user, target_id):
    active_user(db, target_id)
    if user.id == target_id:
        raise AppError(422, "friend_self", "不能添加自己为好友")
    low, high = pair(user.id, target_id)
    # Locking the canonical row serializes opposite-direction requests. The
    # unique pair remains authoritative if the relationship did not exist yet.
    db.execute(insert(Friendship).values(id=uuid4(), created_at=utcnow(), user_low_id=low,
        user_high_id=high, requester_id=user.id, status="pending", revision=1, updated_at=utcnow())
        .on_conflict_do_nothing(constraint="uq_friendship_pair"))
    row = relationship_row(db, low, high, lock=True)
    if row.status == "accepted":
        raise AppError(409, "already_friends", "已经是好友")
    if row.status == "pending":
        if row.requester_id != user.id:
            raise AppError(409, "incoming_request_exists", "对方已申请，请到收到的申请中处理")
        return request_output(db, user, row)
    row.requester_id, row.status = user.id, "pending"
    row.revision += 1
    row.updated_at = utcnow()
    db.flush()
    return request_output(db, user, row)


def respond_friendship(db, user, request_id, data):
    row = db.scalar(select(Friendship).where(Friendship.id == request_id,
        or_(Friendship.user_low_id == user.id, Friendship.user_high_id == user.id)).with_for_update())
    if row is None or row.requester_id == user.id:
        raise not_found()
    active_user(db, row.requester_id)
    revision_matches(row, data.revision)
    if row.status != "pending":
        raise AppError(409, "request_not_pending", "申请已处理，请重新加载")
    row.status = "accepted" if data.decision == "accept" else "rejected"
    row.revision += 1
    row.updated_at = utcnow()
    db.flush()
    return request_output(db, user, row)


def relationship_list(db, user, *, friends=False, direction="incoming", cursor=None, limit=20):
    scope = cursors.scope_key("social-relationships", user.id, friends, direction)
    other_id = select(User.id).where(User.status == "active")
    stmt = select(Friendship).where(
        or_(Friendship.user_low_id == user.id, Friendship.user_high_id == user.id),
        Friendship.user_low_id.in_(other_id), Friendship.user_high_id.in_(other_id),
        Friendship.status == ("accepted" if friends else "pending"))
    if not friends:
        stmt = stmt.where(Friendship.requester_id != user.id if direction == "incoming" else Friendship.requester_id == user.id)
    stmt, cutoff = pagination(stmt, Friendship, scope=scope, cursor=cursor, limit=limit)
    rows = db.scalars(stmt).all()
    return page_result(rows, cutoff, scope, limit, lambda row: request_output(db, user, row))


def remove_friendship(db, user, target_id):
    row = relationship_row(db, user.id, target_id, lock=True)
    if row is None or row.status != "accepted":
        raise not_found()
    row.status, row.updated_at = "removed", utcnow()
    row.revision += 1
    # Re-adding someone must never silently revive old note permissions.
    db.execute(update(NoteGrant).where(NoteGrant.revoked_at.is_(None), or_(
        and_(NoteGrant.owner_id == user.id, NoteGrant.grantee_id == target_id),
        and_(NoteGrant.owner_id == target_id, NoteGrant.grantee_id == user.id)))
        .values(revoked_at=utcnow(), revision=NoteGrant.revision + 1))


def set_follow(db, user, target_id, enabled):
    active_user(db, target_id)
    if user.id == target_id:
        raise AppError(422, "follow_self", "不能关注自己")
    if enabled:
        db.execute(insert(Follow).values(id=uuid4(), created_at=utcnow(), follower_id=user.id, followee_id=target_id)
            .on_conflict_do_nothing(constraint="uq_follow_pair"))
    else:
        db.execute(delete(Follow).where(Follow.follower_id == user.id, Follow.followee_id == target_id))


def grant_output(db, viewer, grant, note=None):
    note = note or db.get(Note, grant.note_id)
    return {"id": grant.id, "note_id": grant.note_id, "owner": member_output(db, viewer, active_user(db, grant.owner_id)),
        "recipient": member_output(db, viewer, active_user(db, grant.grantee_id)), "title": note.title,
        "kind": note.kind, "source_revision": note.revision, "revision": grant.revision,
        "created_at": grant.created_at, "revoked_at": grant.revoked_at}


def create_grant(db, user, data):
    note = owned(db, user, Note, data.note_id)
    revision_matches(note, data.note_revision)
    active_user(db, data.grantee_id)
    relation = relationship_row(db, user.id, data.grantee_id, lock=True)
    if not relation or relation.status != "accepted":
        raise AppError(409, "friend_required", "请先建立好友关系")
    grant = db.scalar(select(NoteGrant).where(NoteGrant.note_id == note.id, NoteGrant.grantee_id == data.grantee_id).with_for_update())
    if grant is None:
        grant = NoteGrant(owner_id=user.id, note_id=note.id, grantee_id=data.grantee_id)
        db.add(grant)
    elif grant.revoked_at is not None:
        grant.revoked_at = None
        grant.revision += 1
    db.flush()
    return grant_output(db, user, grant, note)


def revoke_grant(db, user, grant_id, revision):
    grant = db.scalar(select(NoteGrant).where(NoteGrant.id == grant_id, NoteGrant.owner_id == user.id).with_for_update())
    if grant is None:
        raise not_found()
    revision_matches(grant, revision)
    if grant.revoked_at is None:
        grant.revoked_at = utcnow()
        grant.revision += 1


def authorized_grant(db, user, grant_id):
    grant = db.get(NoteGrant, grant_id)
    if grant is None or user.id not in {grant.owner_id, grant.grantee_id} or grant.revoked_at is not None:
        raise not_found()
    active_user(db, grant.owner_id)
    active_user(db, grant.grantee_id)
    if not are_friends(db, grant.owner_id, grant.grantee_id):
        raise not_found()
    note = db.scalar(select(Note).where(Note.id == grant.note_id, Note.owner_id == grant.owner_id, Note.deleted_at.is_(None)))
    if note is None:
        raise not_found()
    return grant, note


def grant_list(db, user, *, direction="received", cursor=None, limit=20):
    scope = cursors.scope_key("social-shares", user.id, direction)
    low = func.least(NoteGrant.owner_id, NoteGrant.grantee_id)
    high = func.greatest(NoteGrant.owner_id, NoteGrant.grantee_id)
    friend = exists().where(Friendship.user_low_id == low, Friendship.user_high_id == high, Friendship.status == "accepted")
    active = select(User.id).where(User.status == "active")
    stmt = select(NoteGrant).join(Note, and_(Note.id == NoteGrant.note_id, Note.owner_id == NoteGrant.owner_id)).where(
        NoteGrant.owner_id == user.id if direction == "sent" else NoteGrant.grantee_id == user.id,
        NoteGrant.revoked_at.is_(None), Note.deleted_at.is_(None), friend,
        NoteGrant.owner_id.in_(active), NoteGrant.grantee_id.in_(active))
    stmt, cutoff = pagination(stmt, NoteGrant, scope=scope, cursor=cursor, limit=limit)
    return page_result(db.scalars(stmt).all(), cutoff, scope, limit, lambda row: grant_output(db, user, row))


def shared_note(db, user, grant_id):
    grant, note = authorized_grant(db, user, grant_id)
    pages = []
    for page, file in db.execute(select(NotePage, File).join(File, and_(File.id == NotePage.file_id, File.owner_id == NotePage.owner_id))
            .where(NotePage.note_id == note.id, NotePage.owner_id == note.owner_id,
                   NotePage.deleted_at.is_(None)).order_by(NotePage.position, NotePage.id)):
        pages.append({"id": page.id, "position": page.position, "revision": page.revision,
            "filename": file.filename, "width": file.width, "height": file.height,
            "status": file.status, "image_url": f"/api/v1/social/shares/{grant.id}/pages/{page.id}/image"})
    return {"grant": grant_output(db, user, grant, note), "body_json": note.body_json,
        "plain_text": note.plain_text, "pages": pages}


def shared_image(db, user, grant_id, page_id, kind):
    grant, note = authorized_grant(db, user, grant_id)
    page = db.scalar(select(NotePage).where(NotePage.id == page_id, NotePage.note_id == note.id,
                                          NotePage.owner_id == note.owner_id, NotePage.deleted_at.is_(None)))
    if page is None:
        raise not_found()
    file = db.scalar(select(File).where(File.id == page.file_id, File.owner_id == note.owner_id, File.status == "ready"))
    if file is None:
        raise AppError(409, "image_not_ready", "图片尚未准备好")
    variant = db.scalar(select(FileVariant).where(FileVariant.file_id == file.id, FileVariant.kind == kind))
    if variant is None:
        raise AppError(409, "image_not_ready", "阅读图尚未准备好")
    try:
        content = storage().read(variant.object_key)
    except (OSError, StorageUnavailable):
        raise AppError(503, "storage_unavailable", "图片暂时无法读取") from None
    return content, "image/jpeg"


def counts(db, user):
    active = select(User.id).where(User.status == "active")
    relations = select(Friendship).where(or_(Friendship.user_low_id == user.id, Friendship.user_high_id == user.id),
        Friendship.user_low_id.in_(active), Friendship.user_high_id.in_(active))
    return {"friends": db.scalar(select(func.count()).select_from(relations.where(Friendship.status == "accepted").subquery())),
        "incoming_requests": db.scalar(select(func.count()).select_from(relations.where(Friendship.status == "pending", Friendship.requester_id != user.id).subquery())),
        "following": db.scalar(select(func.count()).select_from(Follow).where(Follow.follower_id == user.id, Follow.followee_id.in_(active))),
        "received_shares": _received_count(db, user)}


def _received_count(db, user):
    active = select(User.id).where(User.status == "active")
    friend = exists().where(Friendship.user_low_id == func.least(NoteGrant.owner_id, NoteGrant.grantee_id),
        Friendship.user_high_id == func.greatest(NoteGrant.owner_id, NoteGrant.grantee_id), Friendship.status == "accepted")
    return db.scalar(select(func.count()).select_from(NoteGrant).join(Note, and_(Note.id == NoteGrant.note_id, Note.owner_id == NoteGrant.owner_id))
        .where(NoteGrant.grantee_id == user.id, NoteGrant.revoked_at.is_(None), Note.deleted_at.is_(None),
            NoteGrant.owner_id.in_(active), friend))
