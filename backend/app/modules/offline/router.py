from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.modules.exports.service import check_session
from app.modules.offline import assets, schemas as s, service, snapshot
from app.shared.dependencies import CurrentUser, DB, current_user
from app.shared.errors import AppError

router = APIRouter(prefix="/offline", tags=["offline"])


@router.post("/snapshot", response_model=s.SnapshotOut)
def create_snapshot(data: s.SnapshotIn, request: Request, db: DB, user: CurrentUser):
    owner_id, session_id = user.id, request.state.auth_session.id
    # Snapshot's POST dependency already holds the owner's mutation lock. Create
    # this instance's nonportable generation once, then release that connection
    # before the independent read-only snapshot (also safe with a one-slot pool).
    try:
        service.ensure_state(db, user)
        current_user(request, db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    check_session(owner_id, session_id)
    try:
        result = snapshot.create(owner_id, data)
    except SQLAlchemyError:
        raise AppError(503, "offline_snapshot_unavailable", "离线资料暂不可用，请稍后重试") from None
    check_session(owner_id, session_id)
    return result


@router.get("/files/{file_id}/content", response_class=Response)
def asset(file_id: UUID, request: Request, db: DB, user: CurrentUser,
          kind: Literal["original", "reading", "thumbnail"] = "original"):
    owner_id, session_id = user.id, request.state.auth_session.id
    data, mime, sha256 = assets.read(db, user, file_id, kind)
    db.rollback()
    check_session(owner_id, session_id)
    return Response(content=data, media_type=mime, headers={"Cache-Control": "no-store",
        "Content-Disposition": "attachment" if mime == "application/pdf" else "inline",
        "X-Content-SHA256": sha256, "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff"})


@router.post("/sync", response_model=s.SyncOut, responses={409: {"model": s.SyncConflict}})
def sync(data: s.SyncIn, request: Request, db: DB, user: CurrentUser):
    try:
        result = service.apply(db, user, data)
        # The owner lock prevents concurrent disable/reset/revoke; re-read expiry
        # and session state after any slow file validation before the one commit.
        current_user(request, db)
        db.commit()
        return result
    except AppError as error:
        db.rollback()
        if error.status != 409:
            raise
        # Revalidate before returning current private content after a rollback.
        verified = current_user(request, db)
        current = None if error.code in {"operation_conflict", "resource_conflict", "offline_epoch_changed"} else service.current_state(db, verified, data)
        body = s.SyncConflict(code=error.code, message=error.message,
            request_id=request.state.request_id, operation_id=data.operation_id, current=current)
        return JSONResponse(status_code=409, content=body.model_dump(mode="json"))
    except Exception:
        db.rollback()
        raise
