import json
from sqlalchemy import func, select
from pydantic import ValidationError
from app.modules.learning.models import Card, Deck
from app.modules.learning import card_schemas as s, card_sources
from app.modules.learning import review_service
from app.shared.db import utcnow
from app.shared.errors import AppError


def owned(db, model, owner, row_id, *, lock=False):
    query = select(model).where(model.id == row_id, model.owner_id == owner)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if not row:
        raise AppError(404, "learning_not_found", "学习内容不存在或无权访问")
    return row


def active_deck(db, owner, deck_id):
    row = owned(db, Deck, owner, deck_id, lock=True)
    if row.status != "active":
        raise AppError(409, "deck_archived", "卡片集已归档，请先恢复")
    return row


def revision(row, expected):
    if row.revision != expected:
        raise AppError(409, "revision_conflict", "内容已在其他页面更新，请保留草稿并重新加载")


def output(db, row, *, source_cache=None):
    # A list response checks each distinct frozen source once. Strong validation
    # on source reads, edits, confirmation and materialization remains uncached.
    key = (row.owner_id, json.dumps(row.source, sort_keys=True))
    cached = source_cache.get(key) if source_cache is not None else None
    if cached is None:
        valid, error = True, None
        try:
            card_sources.validate(db, row.owner_id, row.source)
        except AppError as exc:
            valid, error = False, exc.code
        if source_cache is not None:
            source_cache[key] = (valid, error)
    else:
        valid, error = cached
    return s.CardOut(**{key: getattr(row, key) for key in (
        "id", "deck_id", "kind", "front", "back", "payload", "status", "revision", "generation_id", "created_at", "updated_at")},
        source=card_sources.public(row.source), source_valid=valid, source_error=error)


def create_deck(db, owner, data):
    row = Deck(owner_id=owner, name=data.name)
    db.add(row)
    db.commit()
    return row


def patch_deck(db, owner, deck_id, data):
    review_service.lock_owner(db, owner)
    row = owned(db, Deck, owner, deck_id, lock=True)
    revision(row, data.revision)
    if "name" in data.model_fields_set:
        if data.name is None:
            raise AppError(422, "validation_error", "卡片集名称不能为空")
        row.name = s.DeckCreate(name=data.name).name
    if "status" in data.model_fields_set:
        if data.status is None:
            raise AppError(422, "validation_error", "状态不能为空")
        row.status = data.status
    row.revision += 1
    row.updated_at = utcnow()
    if row.status == "archived":
        review_service.pause_cards(db, owner, list(db.scalars(select(Card.id).where(Card.owner_id == owner, Card.deck_id == row.id))), "deck_archived")
    db.commit()
    return row


def create_card(db, owner, data):
    review_service.lock_owner(db, owner)
    active_deck(db, owner, data.deck_id)
    source = card_sources.freeze(db, owner, data.source)
    card_sources.assert_masks(data, source)
    row = Card(owner_id=owner, source=source, **data.model_dump(exclude={"source"}))
    db.add(row)
    db.flush()
    if row.status == "confirmed":
        review_service.ensure_card_plan(db, owner, row)
    db.commit()
    return row


def patch_card(db, owner, card_id, data):
    review_service.lock_owner(db, owner)
    row = owned(db, Card, owner, card_id, lock=True)
    previous_status = row.status
    revision(row, data.revision)
    values = data.model_dump(exclude_unset=True, exclude={"revision", "source"})
    merged = {key: values.get(key, getattr(row, key)) for key in ("kind", "front", "back", "payload")}
    try:
        content = s.CardContent.model_validate(merged)
    except ValidationError:
        raise AppError(422, "validation_error", "卡片类型或内容格式不正确") from None
    target_status = values.get("status", row.status)
    # Archiving stays possible when a deck/source is no longer usable.
    if target_status != "archived":
        active_deck(db, owner, values.get("deck_id", row.deck_id))
    elif "deck_id" in values:
        owned(db, Deck, owner, values["deck_id"])
    source = card_sources.freeze(db, owner, data.source) if "source" in data.model_fields_set else row.source
    if "source" in data.model_fields_set or target_status != "archived" and (content.kind == "occlusion" or target_status == "confirmed"):
        card_sources.validate(db, owner, source)
    card_sources.assert_masks(content, source)
    for key, value in values.items():
        setattr(row, key, value)
    row.source = source
    row.revision += 1
    row.updated_at = utcnow()
    db.flush()
    if row.status == "confirmed" and previous_status != "confirmed":
        review_service.ensure_card_plan(db, owner, row)
    elif row.status != "confirmed":
        review_service.pause_cards(db, owner, [row.id], "card_" + row.status)
    db.commit()
    return row


def batch_review(db, owner, data):
    review_service.lock_owner(db, owner)
    rows = list(db.scalars(select(Card).where(Card.owner_id == owner, Card.id.in_([item.id for item in data.cards]))
                           .order_by(Card.id).with_for_update().execution_options(populate_existing=True)))
    by_id = {row.id: row for row in rows}
    if len(rows) != len(data.cards):
        raise AppError(404, "learning_not_found", "部分卡片不存在或无权访问")
    for item in data.cards:
        row = by_id[item.id]
        revision(row, item.revision)
        if data.action == "confirm":
            active_deck(db, owner, row.deck_id)
            card_sources.validate(db, owner, row.source)
            card_sources.assert_masks(s.CardContent(kind=row.kind, front=row.front, back=row.back, payload=row.payload), row.source)
    for row in rows:
        row.status = "confirmed" if data.action == "confirm" else "archived"
        row.revision += 1
        row.updated_at = utcnow()
    db.flush()
    for row in rows:
        if row.status == "confirmed":
            review_service.ensure_card_plan(db, owner, row)
    if data.action == "archive":
        review_service.pause_cards(db, owner, [row.id for row in rows], "card_archived")
    db.commit()
    return [by_id[item.id] for item in data.cards]


def listing(db, owner, model, offset, limit, **filters):
    conditions = [model.owner_id == owner]
    conditions.extend(getattr(model, key) == value for key, value in filters.items() if value is not None)
    total = db.scalar(select(func.count()).select_from(model).where(*conditions))
    order = model.updated_at if hasattr(model, "updated_at") else model.created_at
    rows = list(db.scalars(select(model).where(*conditions).order_by(order.desc(), model.id.desc()).offset(offset).limit(limit)))
    return rows, total
