"""Owner-authorized inputs; rank only the live, publicly visible community set."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, func, or_, select, tuple_

from app.modules.community import service as community
from app.modules.community.models import CommunityPost, CommunitySnapshot
from app.modules.learning.event_models import LearningEvent
from app.modules.notes import cursors
from app.modules.notes.models import Note, NoteTag, Tag
from app.modules.personalization import preferences_service
from app.modules.personalization.contract import digest
from app.shared.db import utcnow
from app.shared.errors import AppError
from app.shared.models import User


def signals(db, owner, enabled, now):
    if not enabled:
        return [], 'disabled'
    # Bound event selection before joining tags; never inspect any note body/title.
    day = now.date()
    start = datetime.combine(day - timedelta(days=30), datetime.min.time(), tzinfo=timezone.utc)
    recent = list(db.execute(select(LearningEvent.id, LearningEvent.note_id).where(
        LearningEvent.owner_id == owner, LearningEvent.started_at >= start,
        LearningEvent.started_at <= now).order_by(LearningEvent.started_at.desc(), LearningEvent.id.desc()).limit(200)))
    notes = {note_id for _, note_id in recent if note_id is not None}
    terms = []
    if notes:
        terms = list(db.scalars(select(Tag.name).join(NoteTag, and_(NoteTag.tag_id == Tag.id,
            NoteTag.owner_id == owner)).join(Note, and_(Note.id == NoteTag.note_id, Note.owner_id == owner)).where(
            Tag.owner_id == owner, Note.id.in_(notes), Note.deleted_at.is_(None))
            .group_by(Tag.name).order_by(func.count().desc(), Tag.name).limit(20)))
    fingerprint = digest({'day': day.isoformat(), 'events': [str(identity) for identity, _ in recent], 'tags': terms})
    return terms, fingerprint


def reason(snapshot, preferences, learned_tags):
    result = []
    if snapshot.subject in preferences.subjects:
        result.append('本人手动学科兴趣：' + snapshot.subject)
    for tag in preferences.tags:
        if tag in snapshot.tags:
            result.append('本人手动标签兴趣：' + tag)
    for tag in learned_tags:
        if tag in snapshot.tags:
            result.append('已授权本人近30天最多200条活动所涉笔记标签：' + tag + '（活动不代表掌握）')
    return '；'.join(result) if result else '未匹配个人兴趣；按发布时间展示'


def feed(db, viewer, *, cursor=None, limit=20):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise AppError(422, 'recommendation_limit_invalid', '每页只能读取1至50条')
    row, preferences = preferences_service.get(db, viewer.id)
    if not preferences.recommendations_enabled:
        try:
            latest = community.feed(db, viewer, mode='latest', cursor=cursor, limit=limit)
        except AppError as error:
            if error.code == 'cursor_invalid':
                raise AppError(409, 'recommendations_reset_required', '推荐设置已变化，请从最新列表重新开始') from None
            raise
        for item in latest['items']:
            item['recommendation_reason'] = '个人推荐已关闭；按发布时间展示'
        return {**latest, 'mode': 'latest', 'reasons_used': [], 'signals_fingerprint': None}
    now = utcnow()
    learned_tags, fingerprint = signals(db, viewer.id, preferences.learning_signals_enabled, now)
    revision = row.revision if row else 0
    scope = cursors.scope_key('personalized-community', viewer.id)
    cutoff, anchor = now, None
    if cursor:
        try:
            data = cursors.decode_cursor(cursor, {'v', 'scope', 'cutoff', 'at', 'id', 'rank', 'revision', 'signals'}, scope)
        except AppError:
            raise AppError(409, 'recommendations_reset_required', '推荐设置或分页范围已变化，请从第一页重新读取') from None
        if type(data['revision']) is not int or data['revision'] != revision or data['signals'] != fingerprint:
            raise AppError(409, 'recommendations_reset_required', '本人偏好或学习信号已变化，请从第一页重新读取')
        cutoff, at, identity = cursors.note_position(cursors.encode_cursor({key: data[key]
            for key in ('v', 'scope', 'cutoff', 'at', 'id')}), scope)
        if type(data['rank']) is not int or not 0 <= data['rank'] <= 170:
            raise cursors.invalid_cursor()
        anchor = (data['rank'], at, identity)
    rank = case((CommunitySnapshot.subject.in_(preferences.subjects), 50), else_=0)
    for tag in preferences.tags:
        rank = rank + case((CommunitySnapshot.tags.contains([tag]), 4), else_=0)
    for tag in learned_tags:
        rank = rank + case((CommunitySnapshot.tags.contains([tag]), 2), else_=0)
    statement = select(CommunityPost, CommunitySnapshot, rank.label('personal_rank')).join(CommunitySnapshot,
        and_(CommunitySnapshot.id == CommunityPost.live_snapshot_id,
             CommunitySnapshot.post_id == CommunityPost.id, CommunitySnapshot.owner_id == CommunityPost.owner_id))
    statement = statement.join(User, User.id == CommunityPost.owner_id).where(
        CommunityPost.status == 'published', User.status == 'active', CommunitySnapshot.status == 'ready',
        CommunityPost.published_at <= cutoff, CommunitySnapshot.created_at <= cutoff,
        # A later live replacement/edit is removed instead of moving between pages.
        CommunityPost.updated_at <= cutoff)
    if anchor:
        statement = statement.where(tuple_(rank, CommunityPost.published_at, CommunityPost.id) < anchor)
    rows = list(db.execute(statement.order_by(rank.desc(), CommunityPost.published_at.desc(),
                                             CommunityPost.id.desc()).limit(limit + 1)))
    more, rows = len(rows) > limit, rows[:limit]
    items = []
    for post, snapshot, score in rows:
        # Recheck withdrawal/moderation/account state through the existing public authority.
        try:
            public, live = community.public_post(db, post.id)
            if live.id != snapshot.id:
                continue
            items.append({**community.summary(db, viewer, public, live),
                          'recommendation_reason': reason(live, preferences, learned_tags)})
        except AppError as error:
            if error.status != 404:
                raise
    next_cursor = None
    if more and rows:
        post, snapshot, score = rows[-1]
        next_cursor = cursors.encode_cursor({'v': 1, 'scope': scope, 'cutoff': cutoff.isoformat(),
            'at': post.published_at.isoformat(), 'id': str(post.id), 'rank': score,
            'revision': revision, 'signals': fingerprint})
    return {'items': items, 'next_cursor': next_cursor, 'limit': limit, 'mode': 'personalized',
            'reasons_used': ['manual_subjects', 'manual_tags'] + (['own_recent_note_tags'] if preferences.learning_signals_enabled else []),
            'signals_fingerprint': fingerprint}
