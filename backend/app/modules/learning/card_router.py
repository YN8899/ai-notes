from uuid import UUID
from fastapi import APIRouter, Query
from app.modules.learning import card_schemas as s, card_service as service, card_sources, card_generation as generation
from app.modules.learning.models import Card, Deck, CardGeneration
from app.shared.dependencies import DB, CurrentUser

router = APIRouter(prefix="/learning", tags=["learning-cards"])


@router.get("/decks", response_model=s.DeckList)
def decks(db: DB, user: CurrentUser, status: str | None = Query(default=None, pattern="^(active|archived)$"),
          offset: int = Query(default=0, ge=0, le=100000), limit: int = Query(default=50, ge=1, le=50)):
    items, total = service.listing(db, user.id, Deck, offset, limit, status=status)
    return dict(items=items, total=total, offset=offset, limit=limit)


@router.post("/decks", response_model=s.DeckOut, status_code=201)
def create_deck(data: s.DeckCreate, db: DB, user: CurrentUser):
    return service.create_deck(db, user.id, data)


@router.patch("/decks/{deck_id}", response_model=s.DeckOut)
def patch_deck(deck_id: UUID, data: s.DeckPatch, db: DB, user: CurrentUser):
    return service.patch_deck(db, user.id, deck_id, data)


@router.get("/cards", response_model=s.CardList)
def cards(db: DB, user: CurrentUser, deck_id: UUID | None = None, status: s.Status | None = None, kind: s.Kind | None = None,
          offset: int = Query(default=0, ge=0, le=100000), limit: int = Query(default=50, ge=1, le=50)):
    if deck_id:
        service.owned(db, Deck, user.id, deck_id)
    rows, total = service.listing(db, user.id, Card, offset, limit, deck_id=deck_id, status=status, kind=kind)
    source_cache = {}
    return dict(items=[service.output(db, row, source_cache=source_cache) for row in rows], total=total, offset=offset, limit=limit)


@router.post("/cards", response_model=s.CardOut, status_code=201)
def create_card(data: s.CardCreate, db: DB, user: CurrentUser):
    return service.output(db, service.create_card(db, user.id, data))


@router.post("/cards/batch-review", response_model=list[s.CardOut])
def batch_review(data: s.BatchReview, db: DB, user: CurrentUser):
    return [service.output(db, row) for row in service.batch_review(db, user.id, data)]


@router.get("/cards/{card_id}", response_model=s.CardOut)
def card(card_id: UUID, db: DB, user: CurrentUser):
    return service.output(db, service.owned(db, Card, user.id, card_id))


@router.patch("/cards/{card_id}", response_model=s.CardOut)
def patch_card(card_id: UUID, data: s.CardPatch, db: DB, user: CurrentUser):
    return service.output(db, service.patch_card(db, user.id, card_id, data))


@router.get("/cards/{card_id}/source", response_model=s.SourceRead)
def source(card_id: UUID, db: DB, user: CurrentUser):
    return card_sources.read(db, user.id, service.owned(db, Card, user.id, card_id).source)


@router.post("/card-generations/preview", response_model=s.GenerationPreviewOut)
def preview(data: s.GenerationPreview, db: DB, user: CurrentUser):
    return generation.preview(db, user.id, data)


@router.post("/card-generations", response_model=s.GenerationOut, status_code=201)
def create_generation(data: s.GenerationCreate, db: DB, user: CurrentUser):
    return generation.output(db, generation.create(db, user.id, data))


@router.get("/card-generations", response_model=s.GenerationList)
def generations(db: DB, user: CurrentUser, deck_id: UUID | None = None,
                offset: int = Query(default=0, ge=0, le=100000), limit: int = Query(default=50, ge=1, le=50)):
    rows, total = service.listing(db, user.id, CardGeneration, offset, limit, deck_id=deck_id)
    # Polling may publish locally completed drafts; it never schedules or invokes a model.
    rows = [generation.materialize(db, row.id) for row in rows]
    db.commit()
    return dict(items=[generation.output(db, row) for row in rows], total=total, offset=offset, limit=limit)


@router.get("/card-generations/{generation_id}", response_model=s.GenerationOut)
def get_generation(generation_id: UUID, db: DB, user: CurrentUser):
    row = service.owned(db, CardGeneration, user.id, generation_id)
    row = generation.materialize(db, row.id)
    db.commit()
    return generation.output(db, row)
