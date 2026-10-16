import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.modules.ai import chat_service as service, schemas as s
from app.modules.ai.models import AiConversation, AiRun
from app.shared.db import utcnow
from app.shared.dependencies import CurrentUser, DB
from app.shared.errors import AppError

router = APIRouter(tags=["ai-chat"])


@router.get("/ai/conversations", response_model=list[s.ConversationOut])
def list_conversations(db: DB, user: CurrentUser):
    return list(db.scalars(select(AiConversation).where(AiConversation.owner_id == user.id)
                           .order_by(AiConversation.created_at.desc()).limit(100)))


@router.post("/ai/conversations", response_model=s.ConversationOut, status_code=201)
def create_conversation(data: s.ConversationCreate, db: DB, user: CurrentUser):
    row = db.scalar(select(AiConversation).where(AiConversation.owner_id == user.id, AiConversation.request_key == data.request_key))
    if not row:
        row = AiConversation(owner_id=user.id, request_key=data.request_key)
        db.add(row)
        db.commit()
    return row


@router.get("/ai/conversations/{conversation_id}", response_model=s.ConversationDetail)
def detail(conversation_id: UUID, db: DB, user: CurrentUser):
    row = service.conversation(db, user.id, conversation_id)
    return s.ConversationDetail(**s.ConversationOut.model_validate(row).model_dump(),
                                runs=[s.RunOut.model_validate(r) for r in service.runs(db, user.id, row.id)])


@router.post("/ai/conversations/{conversation_id}/runs", response_model=s.RunOut, status_code=201)
def create_run(conversation_id: UUID, data: s.RunCreate, db: DB, user: CurrentUser):
    return service.create_run(db, user.id, conversation_id, data)


@router.get("/ai/runs/{run_id}", response_model=s.RunOut)
def run_detail(run_id: UUID, db: DB, user: CurrentUser):
    return service.get_run(db, user.id, run_id)


@router.post("/ai/runs/{run_id}/cancel", response_model=s.RunOut)
def cancel(run_id: UUID, db: DB, user: CurrentUser):
    # current_user already holds the owner lock; use the same order as worker saves.
    row = db.scalar(select(AiRun).where(AiRun.id == run_id, AiRun.owner_id == user.id).with_for_update())
    if not row:
        raise AppError(404, "run_not_found", "对话运行不存在")
    if row.status in service.ACTIVE:
        row.status, row.finished_at, row.lease_until = "cancelled", utcnow(), None
        db.commit()
    return row


def event(name, data):
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/ai/runs/{run_id}/events", response_class=StreamingResponse,
            responses={200: {"content": {"text/event-stream": {}}, "description": "Persisted meta/delta/state/done events; GET never invokes the model"}})
def events(run_id: UUID, request: Request, db: DB, user: CurrentUser):
    initial = s.RunOut.model_validate(service.get_run(db, user.id, run_id))
    owner, session_id = user.id, request.state.auth_session.id
    db.commit()  # No database connection or lock held for the lifetime of the stream.

    async def stream():
        current, offset, last_state = initial, len(initial.answer), None
        yield event("meta", initial.model_dump(mode="json"))
        while not await request.is_disconnected():
            try:
                current = await run_in_threadpool(service.snapshot, owner, run_id, session_id)
            except AppError as error:
                yield event("error", {"code": error.code, "message": error.message})
                return
            if len(current.answer) > offset:
                yield event("delta", {"offset": offset, "text": current.answer[offset:]})
                offset = len(current.answer)
            state = (current.status, current.usage, current.error_code)
            if state != last_state:
                yield event("state", {"status": current.status, "usage": current.usage, "error_code": current.error_code})
                last_state = state
            if current.status not in service.ACTIVE:
                yield event("done", current.model_dump(mode="json"))
                return
            yield ": keep-alive\n\n"
            await asyncio.sleep(0.4)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})
