"""One owner lock precedes source/plan locks; every mutation appends a receipt.

No provider calls, quiz-score conversions, or passive-reading progress updates.
"""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from uuid import uuid4
from sqlalchemy import and_, exists, func, or_, select
from app.modules.learning import review_schemas as s, scheduler
from app.modules.learning.models import Card, Deck
from app.modules.learning.quiz_models import QuizIdentity
from app.modules.learning.review_models import ReviewItem, ReviewLog, ReviewPlan
from app.modules.notes.models import Note
from app.shared.db import utcnow
from app.shared.errors import AppError
from app.shared.models import User


def lock_owner(db, owner):
    user = db.scalar(select(User).where(User.id == owner).with_for_update().execution_options(populate_existing=True))
    if user is None or user.status != 'active':
        raise AppError(401, 'login_required', '账号状态已变化，请重新登录')


def owned(db, model, owner, row_id, *, lock=False):
    query = select(model).where(model.owner_id == owner, model.id == row_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None:
        raise AppError(404, 'review_not_found', '复习内容不存在或无权访问')
    return row


def check_revision(plan, expected):
    if plan.revision != expected:
        raise AppError(409, 'revision_conflict', '计划已在其他页面更新，请重新加载')


def source_resource(db, owner, kind, source_id):
    model = {'note': Note, 'card': Card, 'quiz': QuizIdentity}[kind]
    row = owned(db, model, owner, source_id)
    if kind == 'note':
        return row.title, row.deleted_at is None
    if kind == 'quiz':
        return row.title, row.status == 'confirmed' and row.question_count > 0
    deck = owned(db, Deck, owner, row.deck_id)
    return row.front[:200], row.status == 'confirmed' and deck.status == 'active'


def eligible_source(db, owner, source):
    title, eligible = source_resource(db, owner, source.kind, getattr(source, source.kind + '_id'))
    if not eligible:
        raise AppError(409, 'review_source_ineligible', '内容在回收站、未确认或已归档，不能复习')
    return title


def item_output(db, item):
    title, eligible = source_resource(db, item.owner_id, item.kind, getattr(item, item.kind + '_id'))
    return s.ReviewItemOut(id=item.id, kind=item.kind, note_id=item.note_id, card_id=item.card_id,
                           quiz_id=item.quiz_id, title=title, eligible=eligible)


def output(db, plan):
    item = owned(db, ReviewItem, plan.owner_id, plan.item_id)
    return s.ReviewPlanOut(**{key: getattr(plan, key) for key in (
        'id', 'item_id', 'strategy', 'status', 'next_due_at', 'algorithm_version', 'scheduler_state',
        'last_fsrs_state', 'revision', 'created_at', 'updated_at')}, item=item_output(db, item))


def snapshot(plan):
    if plan is None:
        return None
    return {'next_due_at': plan.next_due_at.isoformat(), 'strategy': plan.strategy, 'status': plan.status,
            'algorithm_version': plan.algorithm_version, 'scheduler_state': deepcopy(plan.scheduler_state),
            'last_fsrs_state': deepcopy(plan.last_fsrs_state), 'revision': plan.revision}


def request_hash(action, data, row_id=None):
    body = {'action': action, 'id': str(row_id) if row_id else None, 'body': data.model_dump(mode='json')}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def receipt(log):
    return s.ReviewReceipt(plan=s.ReviewPlanOut.model_validate(log.result_plan) if log.result_plan else None,
                           log=s.ReviewLogOut.model_validate(log))


def replay(db, owner, key, digest):
    previous = db.scalar(select(ReviewLog).where(ReviewLog.owner_id == owner, ReviewLog.request_key == key))
    if previous is None:
        return None
    if previous.request_hash != digest:
        raise AppError(409, 'request_key_conflict', '相同请求标识已用于不同操作，请重新提交')
    return receipt(previous)


def append_log(db, owner, item, plan, key, digest, action, before, *, rating=None, reason=None, now=None):
    now = now or utcnow()
    db.flush()
    row = ReviewLog(owner_id=owner, item_id=item.id, plan_id=plan.id if plan else None,
                    request_key=key, request_hash=digest, action=action, rating=rating,
                    scheduled_for=before and before['next_due_at'], reviewed_at=now,
                    old_snapshot=before, new_snapshot=snapshot(plan), reason=reason,
                    result_plan=output(db, plan).model_dump(mode='json') if plan else None)
    # SQLAlchemy DateTime accepts datetime, never an unparsed JSON string.
    if before:
        from datetime import datetime
        row.scheduled_for = datetime.fromisoformat(before['next_due_at'])
    db.add(row)
    db.flush()
    return row


def get_item(db, owner, source):
    key = source.kind + '_id'
    item = db.scalar(select(ReviewItem).where(ReviewItem.owner_id == owner, getattr(ReviewItem, key) == getattr(source, key)))
    if item is None:
        item = ReviewItem(owner_id=owner, kind=source.kind, **{key: getattr(source, key)})
        db.add(item)
        db.flush()
    return item


def effective_plan(db, owner, item_id):
    return db.scalar(select(ReviewPlan).where(ReviewPlan.owner_id == owner, ReviewPlan.item_id == item_id,
                   ReviewPlan.status.in_(['active', 'paused'])).with_for_update().execution_options(populate_existing=True))


def bounded_due(value, now):
    if not now - timedelta(seconds=5) <= value <= now + timedelta(days=36500):
        raise AppError(422, 'schedule_time_invalid', '排期可选择现在或未来100年内的时间')
    return value


def new_plan(db, owner, item, strategy, due, now):
    pid = uuid4()
    scheduled = scheduler.initialize(strategy, now, card_id=pid.int % 2**63)
    plan = ReviewPlan(id=pid, owner_id=owner, item_id=item.id, strategy=strategy, status='active',
                      next_due_at=due or now, scheduler_state=scheduled.scheduler_state,
                      algorithm_version=scheduled.algorithm_version, revision=1, updated_at=now)
    db.add(plan)
    db.flush()
    return plan


def create(db, owner, data):
    lock_owner(db, owner)
    digest = request_hash('create', data)
    prior = replay(db, owner, data.request_key, digest)
    if prior:
        return prior
    eligible_source(db, owner, data)
    now = utcnow()
    due = bounded_due(data.next_due_at, now) if data.next_due_at else now
    item = get_item(db, owner, data)
    if effective_plan(db, owner, item.id):
        raise AppError(409, 'review_plan_exists', '该内容已有有效复习计划，请修改现有计划')
    plan = new_plan(db, owner, item, data.strategy, due, now)
    log = append_log(db, owner, item, plan, data.request_key, digest, 'create', None, now=now)
    db.commit()
    return receipt(log)


def ensure_card_plan(db, owner, card):
    """Called inside the card transaction after owner lock and confirmation flush."""
    source = s.ReviewSource(kind='card', card_id=card.id)
    item = get_item(db, owner, source)
    if effective_plan(db, owner, item.id):
        return
    now = utcnow()
    plan = new_plan(db, owner, item, 'manual', now, now)
    key = uuid4()
    append_log(db, owner, item, plan, key, request_hash('card_confirmation', source, card.id),
               'create', None, reason='card_confirmed', now=now)


def pause_cards(db, owner, card_ids, reason):
    plans = list(db.scalars(select(ReviewPlan).join(ReviewItem, ReviewItem.id == ReviewPlan.item_id)
        .where(ReviewPlan.owner_id == owner, ReviewItem.card_id.in_(card_ids), ReviewPlan.status == 'active')
        .order_by(ReviewPlan.id).with_for_update(of=ReviewPlan).execution_options(populate_existing=True)))
    for plan in plans:
        before = snapshot(plan)
        plan.status, plan.updated_at = 'paused', utcnow()
        plan.revision += 1
        item = owned(db, ReviewItem, owner, plan.item_id)
        append_log(db, owner, item, plan, uuid4(), hashlib.sha256(f'{reason}:{plan.id}:{plan.revision}'.encode()).hexdigest(),
                   'pause', before, reason=reason)


def patch(db, owner, plan_id, data):
    lock_owner(db, owner)
    digest = request_hash('patch', data, plan_id)
    prior = replay(db, owner, data.request_key, digest)
    if prior:
        return prior
    plan = owned(db, ReviewPlan, owner, plan_id, lock=True)
    check_revision(plan, data.revision)
    if plan.status == 'archived':
        raise AppError(409, 'review_plan_archived', '计划已归档，请重新建立计划')
    item = owned(db, ReviewItem, owner, plan.item_id)
    before, now = snapshot(plan), utcnow()
    operation = data.operation
    if operation == 'resume':
        eligible_source(db, owner, s.ReviewSource.model_validate(item, from_attributes=True))
        if plan.status != 'paused':
            raise AppError(409, 'review_plan_state', '仅暂停的计划可以恢复')
        plan.status = 'active'
    elif operation == 'pause':
        if plan.status != 'active':
            raise AppError(409, 'review_plan_state', '仅启用的计划可以暂停')
        plan.status = 'paused'
    elif operation == 'archive':
        plan.status = 'archived'
    elif operation == 'reschedule':
        plan.next_due_at = bounded_due(data.next_due_at, now)
    elif operation == 'switch':
        expected_version = {'manual': scheduler.MANUAL_VERSION, 'fsrs': scheduler.FSRS_VERSION}.get(plan.strategy)
        if plan.algorithm_version != expected_version or plan.strategy == 'manual' and plan.scheduler_state != {}:
            raise AppError(409, 'scheduler_state_invalid', '原排期算法版本或状态不兼容，请核对计划')
        if data.strategy == plan.strategy:
            raise AppError(409, 'review_strategy_unchanged', '请选择不同的排期策略')
        if plan.strategy == 'fsrs':
            # Validate state before retaining it; never silently repair corrupt data.
            scheduler._card(plan.scheduler_state, now)
            plan.last_fsrs_state = {'algorithm_version': plan.algorithm_version, 'scheduler_state': deepcopy(plan.scheduler_state)}
        if data.strategy == 'fsrs' and data.fsrs_mode == 'resume_last':
            saved = plan.last_fsrs_state
            if not saved or saved.get('algorithm_version') != scheduler.FSRS_VERSION:
                raise AppError(409, 'fsrs_history_unavailable', '没有兼容的历史排期状态，请选择重新开始')
            card = scheduler._card(saved.get('scheduler_state'), now)
            plan.scheduler_state = deepcopy(saved['scheduler_state'])
            plan.algorithm_version = saved['algorithm_version']
            plan.next_due_at = card.due
        else:
            scheduled = scheduler.initialize(data.strategy, now, card_id=plan.id.int % 2**63)
            plan.scheduler_state, plan.algorithm_version = scheduled.scheduler_state, scheduled.algorithm_version
            if data.strategy == 'fsrs':
                plan.next_due_at = now
        plan.strategy = data.strategy
        if data.next_due_at:
            plan.next_due_at = bounded_due(data.next_due_at, now)
    plan.revision += 1
    plan.updated_at = now
    reason = data.reason
    if operation == 'switch':
        reason = f'{reason} [fsrs_mode={data.fsrs_mode or "none"}]'
    log = append_log(db, owner, item, plan, data.request_key, digest, operation, before, reason=reason, now=now)
    db.commit()
    return receipt(log)


def review(db, owner, plan_id, data):
    lock_owner(db, owner)
    digest = request_hash('review', data, plan_id)
    prior = replay(db, owner, data.request_key, digest)
    if prior:
        return prior
    plan = owned(db, ReviewPlan, owner, plan_id, lock=True)
    check_revision(plan, data.revision)
    if plan.status != 'active':
        raise AppError(409, 'review_plan_inactive', '请先恢复该计划')
    item = owned(db, ReviewItem, owner, plan.item_id)
    eligible_source(db, owner, s.ReviewSource.model_validate(item, from_attributes=True))
    before, now = snapshot(plan), utcnow()
    if plan.strategy == 'manual' and not data.reason:
        raise AppError(422, 'review_reason_required', '手动评价需要记录下次时间的原因')
    if plan.strategy == 'fsrs' and data.next_due_at is not None:
        raise AppError(422, 'review_time_unexpected', 'FSRS评价由算法计算下次时间')
    scheduled = scheduler.schedule(plan.strategy, plan.scheduler_state, data.rating, now,
                                   algorithm_version=plan.algorithm_version, manual_due=data.next_due_at)
    plan.next_due_at, plan.scheduler_state = scheduled.next_due_at, scheduled.scheduler_state
    plan.algorithm_version = scheduled.algorithm_version
    plan.revision += 1
    plan.updated_at = now
    log = append_log(db, owner, item, plan, data.request_key, digest, 'review', before,
                     rating=data.rating, reason=data.reason, now=now)
    db.commit()
    return receipt(log)


def practice(db, owner, data):
    lock_owner(db, owner)
    digest = request_hash('practice', data)
    prior = replay(db, owner, data.request_key, digest)
    if prior:
        return prior
    eligible_source(db, owner, data)
    item = get_item(db, owner, data)
    plan = owned(db, ReviewPlan, owner, data.plan_id, lock=True) if data.plan_id else None
    if plan:
        if plan.item_id != item.id:
            raise AppError(404, 'review_not_found', '计划与内容不匹配')
        check_revision(plan, data.revision)
    log = append_log(db, owner, item, plan, data.request_key, digest, 'practice', snapshot(plan), rating=data.rating)
    db.commit()
    return receipt(log)


def eligible_clause():
    return or_(
        and_(ReviewItem.kind == 'note', exists(select(Note.id).where(Note.id == ReviewItem.note_id, Note.owner_id == ReviewItem.owner_id, Note.deleted_at.is_(None)))),
        and_(ReviewItem.kind == 'card', exists(select(Card.id).join(Deck, Deck.id == Card.deck_id).where(Card.id == ReviewItem.card_id, Card.owner_id == ReviewItem.owner_id, Card.status == 'confirmed', Deck.status == 'active'))),
        and_(ReviewItem.kind == 'quiz', exists(select(QuizIdentity.id).where(QuizIdentity.id == ReviewItem.quiz_id, QuizIdentity.owner_id == ReviewItem.owner_id, QuizIdentity.status == 'confirmed', QuizIdentity.question_count > 0))))


def listing(db, owner, *, offset=0, limit=50, kind=None, status=None, due_from=None, due_to=None, due_only=False, eligible_only=False):
    conditions = [ReviewPlan.owner_id == owner]
    if kind:
        conditions.append(ReviewItem.kind == kind)
    if status:
        conditions.append(ReviewPlan.status == status)
    if due_only:
        conditions.extend([ReviewPlan.status == 'active', ReviewPlan.next_due_at <= utcnow()])
    if eligible_only or due_only:
        conditions.append(eligible_clause())
    if due_from:
        conditions.append(ReviewPlan.next_due_at >= due_from)
    if due_to:
        conditions.append(ReviewPlan.next_due_at <= due_to)
    query = select(ReviewPlan).join(ReviewItem, ReviewItem.id == ReviewPlan.item_id).where(*conditions)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(ReviewPlan.next_due_at, ReviewPlan.id).offset(offset).limit(limit))
    return dict(items=[output(db, row) for row in rows], total=total, offset=offset, limit=limit)


def history(db, owner, *, plan_id=None, item_id=None, action=None, offset=0, limit=50):
    if plan_id:
        owned(db, ReviewPlan, owner, plan_id)
    if item_id:
        owned(db, ReviewItem, owner, item_id)
    conditions = [ReviewLog.owner_id == owner]
    conditions.extend(getattr(ReviewLog, key) == value for key, value in {'plan_id': plan_id, 'item_id': item_id, 'action': action}.items() if value is not None)
    total = db.scalar(select(func.count()).select_from(ReviewLog).where(*conditions))
    rows = list(db.scalars(select(ReviewLog).where(*conditions).order_by(ReviewLog.reviewed_at.desc(), ReviewLog.id.desc()).offset(offset).limit(limit)))
    return dict(items=rows, total=total, offset=offset, limit=limit)
