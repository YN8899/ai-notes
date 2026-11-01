from uuid import UUID

from fastapi import APIRouter, Query

from app.modules.search import schemas as s, service
from app.shared.dependencies import CurrentUser, DB

router = APIRouter(tags=["ocr"])


@router.post("/ocr/documents", response_model=s.OcrDocumentOut, status_code=201)
def create_document(data: s.OcrCreate, db: DB, user: CurrentUser):
    return service.output(db, service.create(db, user.id, data))


@router.get("/ocr/documents", response_model=list[s.OcrDocumentOut])
def list_documents(db: DB, user: CurrentUser, note_id: UUID, page_id: UUID | None = None,
                   offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100)):
    return [service.output(db, row) for row in service.listing(db, user.id, note_id, page_id, offset, limit)]


@router.get("/ocr/documents/{document_id}", response_model=s.OcrDocumentOut)
def get_document(document_id: UUID, db: DB, user: CurrentUser):
    return service.output(db, service.owned(db, user.id, document_id))


@router.patch("/ocr/documents/{document_id}", response_model=s.OcrDocumentOut)
def update_document(document_id: UUID, data: s.OcrUpdate, db: DB, user: CurrentUser):
    return service.output(db, service.correct(db, user.id, document_id, data))
