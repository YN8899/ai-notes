from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query

from app.modules.search import keyword_schemas as s, keyword_service as service
from app.shared.dependencies import CurrentUser, DB

router = APIRouter(tags=["search"])


@router.get("/search", response_model=s.SearchPageOut)
def search(db: DB, user: CurrentUser, q: s.Keyword, limit: int = Query(20, ge=1, le=50),
           cursor: str | None = Query(None, max_length=1024), kind: Literal["text", "image"] | None = None,
           notebook_id: UUID | None = None, folder_id: UUID | None = None, tag_id: UUID | None = None):
    return service.search(db, user.id, q, limit, cursor, kind=kind, notebook_id=notebook_id, folder_id=folder_id, tag_id=tag_id)


@router.get("/search/{kind}/{identity}/source", response_model=s.SearchSourceOut)
def source(kind: s.HitKind, identity: UUID, q: s.Keyword, db: DB, user: CurrentUser,
           match_hash: str = Query(..., pattern=r"^[0-9a-f]{64}$"),
           source_revision: int = Query(..., ge=1), ocr_revision: int | None = Query(None, ge=1)):
    return service.resolve(db, user.id, kind, identity, q, source_revision, ocr_revision, match_hash)
