from uuid import UUID
from fastapi import APIRouter
from app.shared.dependencies import DB, CurrentUser
from app.modules.search import semantic_schemas as s, semantic_service as service
from app.modules.search.keyword_schemas import SearchSourceOut

router = APIRouter(prefix="/semantic", tags=["semantic"])


@router.get("/status", response_model=s.SemanticStatusOut)
def status(db: DB, user: CurrentUser):
    return service.status(db, user.id)


@router.get("/preview", response_model=s.SemanticPreviewOut)
def preview(db: DB, user: CurrentUser):
    return service.preview(db, user.id)


@router.get("/indexes", response_model=list[s.SemanticIndexOut])
def indexes(db: DB, user: CurrentUser):
    return service.indexes(db, user.id)


@router.post("/indexes", response_model=s.SemanticIndexOut, status_code=201)
def create_index(data: s.SemanticIndexCreate, db: DB, user: CurrentUser):
    return service.create_index(db, user.id, data)


@router.post("/indexes/{index_id}/activate", response_model=s.SemanticIndexOut)
def activate(index_id: UUID, db: DB, user: CurrentUser):
    return service.activate(db, user.id, index_id)


@router.post("/queries", response_model=s.SemanticQueryOut, status_code=201)
def create_query(data: s.SemanticQueryCreate, db: DB, user: CurrentUser):
    return service.create_query(db, user.id, data)


@router.get("/queries/{query_id}", response_model=s.SemanticQueryOut)
def get_query(query_id: UUID, db: DB, user: CurrentUser):
    return service.get_query(db, user.id, query_id)


@router.get("/chunks/{chunk_id}/source", response_model=SearchSourceOut)
def source(chunk_id: UUID, db: DB, user: CurrentUser):
    return service.source(db, user.id, chunk_id)
