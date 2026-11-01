"""Server-frozen source identity and safe historical source navigation."""
import hashlib
from uuid import UUID
from sqlalchemy import select
from app.modules.ai import image_context, pdf_context, source_context, text_context
from app.modules.ai.image_schemas import ImageInput
from app.modules.ai.text_schemas import TextInput
from app.modules.ai.schemas import PdfInput
from app.modules.files.models import File
from app.modules.learning import card_schemas as s
from app.shared.errors import AppError


def freeze(db, owner, selection):
    if selection is None:
        return None
    values = selection.model_dump(exclude={"kind"})
    if selection.kind == "text":
        return text_context.freeze(db, owner, TextInput(**values))
    implementation, schema = (image_context, ImageInput) if selection.kind == "image" else (pdf_context, PdfInput)
    source = implementation.freeze(db, owner, schema(**values))
    if len(source["pages"]) != 1:
        raise AppError(422, "card_single_page_required", "卡片只支持单页来源")
    page = source["pages"][0]
    file = db.scalar(select(File).where(File.id == UUID(page["file_id"]), File.owner_id == owner))
    # Hash the actual bounded reading variant, independently of the original upload hash.
    data = image_context.read_bounded_jpeg(page, None)[0]
    source = {**source, "_card_identity": {"sha256": file.sha256, "width": file.width,
              "height": file.height, "reading_sha256": hashlib.sha256(data).hexdigest()}}
    return source


def validate(db, owner, source):
    if source is None:
        return None
    try:
        note = source_context.validate(db, owner, source)
        if source_context.is_visual(source):
            if len(source["pages"]) != 1:
                raise ValueError("card_source_page_count")
            page, identity = source["pages"][0], source["_card_identity"]
            file = db.scalar(select(File).where(File.id == UUID(page["file_id"]), File.owner_id == owner))
            if not file or (file.sha256, file.width, file.height) != (identity["sha256"], identity["width"], identity["height"]):
                raise ValueError("card_file_changed")
            data = image_context.read_bounded_jpeg(page, None)[0]
            if hashlib.sha256(data).hexdigest() != identity["reading_sha256"]:
                raise ValueError("card_reading_changed")
        return note
    except (AppError, KeyError, TypeError, ValueError):
        raise AppError(409, "card_source_unavailable", "卡片来源已失效，请核对来源；卡片内容仍保留") from None


def assert_masks(content, source):
    if content.kind != "occlusion":
        return
    if not source or not source_context.is_visual(source) or len(source["pages"]) != 1:
        raise AppError(422, "card_visual_source_required", "遮挡卡片需要有效单页图片或 PDF 来源")
    region = source["pages"][0].get("region") or {"x": 0, "y": 0, "width": 1, "height": 1}
    for mask in content.payload.masks:
        if (mask.x < region["x"] or mask.y < region["y"] or
                mask.x + mask.width > region["x"] + region["width"] + 1e-12 or
                mask.y + mask.height > region["y"] + region["height"] + 1e-12):
            raise AppError(422, "card_mask_outside_source", "遮挡区域必须位于所选来源范围内")


def public(source):
    return source_context.public_source(source)


def read(db, owner, source):
    if source is None:
        raise AppError(404, "card_source_not_found", "卡片没有来源")
    note = validate(db, owner, source)
    if source_context.is_text(source):
        result = text_context.read(db, owner, source)
        return s.SourceRead(**result.model_dump())
    page = source["pages"][0]
    return s.SourceRead(source=public(source), source_changed=note.revision != source["revision"],
                        file_id=page["file_id"], page_id=page["page_id"], page_number=page["number"])
