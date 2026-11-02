"""Single explicit aggregate-only analysis atop the normal durable AI worker."""
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import delete, func, select

from app.modules.ai import compatible
from app.modules.ai.models import AiConversation, AiRun
from app.modules.learning.summary_service import aggregate
from app.modules.personalization import contract, preferences_service, schemas as s
from app.modules.personalization.models import LearningAnalysis, LearningAnalysisPreview
from app.shared.db import utcnow
from app.shared.errors import AppError
from app.shared.models import AuthSession, User

ACTIVE = ('queued', 'running')
PROMPT = '解释本次明确确认的本人学习汇总'


def enabled(db, owner):
    row, content = preferences_service.get(db, owner)
    if not content.ai_analysis_enabled:
        raise AppError(409, 'analysis_disabled', 'AI学习分析已关闭；可以继续查看事实统计')
    return row


def valid_session(db, owner, session_id):
    session = db.get(AuthSession, session_id) if session_id else None
    if not session or session.user_id != owner or session.revoked_at or session.expires_at <= utcnow():
        raise AppError(401, 'session_expired', '原确认会话已失效，请重新登录并预览')


def facts(db, owner, date_from, date_to):
    summary = aggregate(db, owner, date_from, date_to)
    contract.validate_summary(summary)
    return {'summary': summary, 'rules': contract.rules(summary), 'ai_optional': True}


def preview(db, owner, session_id, data):
    preferences = enabled(db, owner)
    valid_session(db, owner, session_id)
    if db.scalar(select(func.count()).select_from(LearningAnalysisPreview).where(
            LearningAnalysisPreview.owner_id == owner,
            LearningAnalysisPreview.created_at > utcnow() - timedelta(hours=1))) >= 60:
        raise AppError(429, 'analysis_preview_limited', '预览次数过多，请稍后再试')
    selected = compatible.resolve(data.provider, data.model, tools=False)
    summary = aggregate(db, owner, data.date_from, data.date_to)
    payload = contract.preview_payload(summary, data.provider, data.model, compatible.freeze(selected))
    input_bytes = sum(len(item['content'].encode('utf-8')) for item in payload['input_messages'])
    if input_bytes > 32000:
        raise AppError(422, 'analysis_input_too_large', '汇总超出单次分析范围')
    db.execute(delete(LearningAnalysisPreview).where(LearningAnalysisPreview.owner_id == owner,
                                                     LearningAnalysisPreview.expires_at <= utcnow()))
    row = LearningAnalysisPreview(owner_id=owner, auth_session_id=session_id,
        preferences_revision=preferences.revision, summary=summary, input_messages=payload['input_messages'],
        provider=data.provider, model=data.model, provider_snapshot=payload['provider_snapshot'],
        preview_hash=contract.digest(payload), input_bytes=input_bytes, expires_at=utcnow() + timedelta(minutes=10))
    db.add(row)
    db.commit()
    return s.AnalysisPreviewOut(id=row.id, preview_hash=row.preview_hash, summary=row.summary,
        provider=row.provider, model=row.model, input_messages=row.input_messages,
        input_bytes=row.input_bytes, expires_at=row.expires_at)


def create(db, owner, session_id, data):
    db.scalar(select(User).where(User.id == owner).with_for_update())
    request_hash = contract.digest(data.model_dump(mode='json'))
    previous = db.scalar(select(LearningAnalysis).where(LearningAnalysis.owner_id == owner,
                                                       LearningAnalysis.request_key == data.request_key))
    if previous:
        if previous.request_hash != request_hash:
            raise AppError(409, 'request_key_conflict', '提交内容已变化，请重新预览并确认')
        return output(db, owner, previous.id)
    preferences = enabled(db, owner)
    valid_session(db, owner, session_id)
    prepared = db.scalar(select(LearningAnalysisPreview).where(LearningAnalysisPreview.id == data.preview_id,
        LearningAnalysisPreview.owner_id == owner).with_for_update())
    if (prepared is None or prepared.auth_session_id != session_id or prepared.expires_at <= utcnow()
            or prepared.preview_hash != data.preview_hash or prepared.preferences_revision != preferences.revision):
        raise AppError(409, 'analysis_preview_changed', '预览已过期、偏好已变化或确认会话不一致，请重新预览')
    payload = contract.preview_payload(prepared.summary, prepared.provider, prepared.model, prepared.provider_snapshot)
    if prepared.input_messages != payload['input_messages'] or contract.digest(payload) != data.preview_hash:
        raise AppError(409, 'analysis_preview_changed', '预览内容已变化，请重新预览')
    compatible.resolve(prepared.provider, prepared.model, tools=False, frozen=prepared.provider_snapshot)
    if db.scalar(select(AiRun.id).where(AiRun.owner_id == owner, AiRun.status.in_(ACTIVE)).limit(1)):
        raise AppError(409, 'run_active', '已有AI运行，请等待或先取消')
    if db.scalar(select(AiRun.id).where(AiRun.owner_id == owner, AiRun.request_key == data.request_key)):
        raise AppError(409, 'request_key_conflict', '提交标识已用于其他AI运行，请重新确认')
    recent = db.scalar(select(func.count()).select_from(AiRun).where(AiRun.owner_id == owner,
                         AiRun.created_at > utcnow() - timedelta(hours=1)))
    if recent >= 30:
        raise AppError(429, 'ai_rate_limited', '每小时最多发送30次，请稍后再试')
    conversation = AiConversation(owner_id=owner, request_key=uuid4(), title='私人学习事实解释')
    db.add(conversation)
    db.flush()
    run = AiRun(owner_id=owner, conversation_id=conversation.id, request_key=data.request_key,
        request_hash=request_hash, provider=prepared.provider, model=prepared.model,
        provider_snapshot=prepared.provider_snapshot, tools=[], tool_scope=None,
        auth_session_id=session_id, prompt=PROMPT, input_messages=prepared.input_messages, source=None)
    db.add(run)
    db.flush()
    analysis = LearningAnalysis(owner_id=owner, run_id=run.id, request_key=data.request_key,
        request_hash=request_hash, preview_hash=data.preview_hash, snapshot=payload)
    db.add(analysis)
    db.delete(prepared)
    db.commit()
    return output(db, owner, analysis.id)


def output(db, owner, identity):
    row = db.scalar(select(LearningAnalysis).where(LearningAnalysis.id == identity, LearningAnalysis.owner_id == owner))
    if row is None:
        raise AppError(404, 'analysis_not_found', '学习分析不存在')
    run = db.scalar(select(AiRun).where(AiRun.id == row.run_id, AiRun.owner_id == owner))
    if run is None:
        raise AppError(409, 'analysis_history_invalid', '学习分析历史不完整')
    # Reading history deliberately does not consult the current model catalog or switches.
    contract.validate_summary(row.snapshot['summary'])
    return s.AnalysisOut(id=row.id, run_id=run.id, summary=row.snapshot['summary'], preview_hash=row.preview_hash,
        provider=run.provider, model=run.model, status=run.status, answer=run.answer, usage=run.usage,
        error_code=run.error_code, created_at=row.created_at, finished_at=run.finished_at)


def history(db, owner, limit=20):
    ids = db.scalars(select(LearningAnalysis.id).where(LearningAnalysis.owner_id == owner)
        .order_by(LearningAnalysis.created_at.desc(), LearningAnalysis.id.desc()).limit(limit))
    return [output(db, owner, identity) for identity in ids]


def cancel(db, owner, identity):
    row = db.scalar(select(LearningAnalysis).where(LearningAnalysis.id == identity, LearningAnalysis.owner_id == owner))
    if row is None:
        raise AppError(404, 'analysis_not_found', '学习分析不存在')
    run = db.scalar(select(AiRun).where(AiRun.id == row.run_id, AiRun.owner_id == owner).with_for_update())
    if run.status in ACTIVE:
        run.status, run.finished_at, run.lease_until = 'cancelled', utcnow(), None
    db.commit()
    return output(db, owner, identity)


def cancel_active(db, owner):
    for run in db.scalars(select(AiRun).join(LearningAnalysis,
            and_owner_run()).where(LearningAnalysis.owner_id == owner, AiRun.status.in_(ACTIVE)).with_for_update(of=AiRun)):
        run.status, run.error_code, run.finished_at, run.lease_until = 'cancelled', 'analysis_disabled', utcnow(), None
    db.execute(delete(LearningAnalysisPreview).where(LearningAnalysisPreview.owner_id == owner))


def and_owner_run():
    from sqlalchemy import and_
    return and_(LearningAnalysis.run_id == AiRun.id, LearningAnalysis.owner_id == AiRun.owner_id)


def is_conversation(db, identity):
    return bool(db.scalar(select(LearningAnalysis.id).join(AiRun, and_owner_run()).where(
        AiRun.conversation_id == identity).limit(1)))


def validate_run(db, run):
    """Called by chat_service from T32 start_attempt AND final paid POST gate."""
    row = db.scalar(select(LearningAnalysis).where(LearningAnalysis.run_id == run.id))
    if row is None:
        return
    enabled(db, run.owner_id)
    valid_session(db, run.owner_id, run.auth_session_id)
    try:
        expected = contract.preview_payload(row.snapshot['summary'], row.snapshot['provider'],
                                           row.snapshot['model'], row.snapshot['provider_snapshot'])
        valid = (row.owner_id == run.owner_id and row.snapshot == expected and row.preview_hash == contract.digest(expected)
                 and run.input_messages == expected['input_messages'] and run.provider == expected['provider']
                 and run.model == expected['model'] and run.provider_snapshot == expected['provider_snapshot']
                 and run.tools == [] and run.tool_scope is None and run.source is None and run.prompt == PROMPT
                 and run.request_hash == row.request_hash and run.request_key == row.request_key)
    except (KeyError, ValueError, TypeError):
        valid = False
    if not valid:
        raise AppError(409, 'analysis_scope_changed', '已确认的本人汇总范围不一致，已停止发送')
