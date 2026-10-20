"""Create an editable note from a server-owned answer, without another model call."""
import re

from sqlalchemy import select

from app.modules.ai import chat_service, schemas as s
from app.modules.ai.models import AiRun, AiSavedNote
from app.modules.notes import service as notes, schemas as ns
from app.modules.notes.models import Note
from app.shared.errors import AppError

CITATION = re.compile(r"\[\[p:([0-9]{1,3})\]\]")


def metadata(saved, note):
    return s.SavedAnswerOut(run_id=saved.run_id, note_id=note.id, note_title=note.title,
                            notebook_id=note.notebook_id, deleted_at=note.deleted_at,
                            saved_at=saved.created_at, source=saved.source,
                            cited_page_numbers=saved.cited_page_numbers)


def list_saved(db, owner, conversation_id):
    chat_service.conversation(db, owner, conversation_id)
    rows = db.execute(select(AiSavedNote, Note).join(Note, Note.id == AiSavedNote.note_id)
                      .join(AiRun, AiRun.id == AiSavedNote.run_id).where(
                          AiSavedNote.owner_id == owner, Note.owner_id == owner,
                          AiRun.owner_id == owner, AiRun.conversation_id == conversation_id)
                      .order_by(AiSavedNote.created_at, AiSavedNote.id))
    return [metadata(saved, note) for saved, note in rows]


def note_provenance(db, user, note_id):
    note = notes.owned(db, user, Note, note_id)
    row = db.execute(select(AiSavedNote, AiRun.model).join(AiRun, AiRun.id == AiSavedNote.run_id)
                     .where(AiSavedNote.note_id == note_id, AiSavedNote.owner_id == user.id,
                            AiRun.owner_id == user.id)).first()
    if not row:
        return None
    saved, model = row
    return s.SavedAnswerProvenanceOut(**metadata(saved, note).model_dump(), model=model)


def note_content(run, source):
    allowed = {page.number for page in source.pages} if source else set()
    cited = sorted({int(match[1]) for match in CITATION.finditer(run.answer) if int(match[1]) in allowed})

    def readable(match):
        number = int(match[1])
        return f"（原 PDF 第 {number} 页）" if number in allowed else match[0]

    answer = CITATION.sub(readable, run.answer)
    # Build literal Tiptap nodes, never parse provider output as HTML or Markdown.
    sections = [("问题", run.prompt), ("AI 回答", answer), ("生成模型", run.model)]
    if source:
        numbers = "、".join(str(page.number) for page in source.pages)
        reference = f"{source.title}\n原 PDF 第 {numbers} 页\n来源版本 {source.revision}"
        if cited:
            reference += "\n回答引用原 PDF 第 " + "、".join(map(str, cited)) + " 页"
        sections.append(("PDF 来源", reference))
    content, plain = [], []
    for title, text in sections:
        content.append({"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": title}]})
        for line in text.split("\n"):
            paragraph = {"type": "paragraph"}
            if line:
                paragraph["content"] = [{"type": "text", "text": line}]
            content.append(paragraph)
        plain.extend([title, text])
    return {"type": "doc", "content": content}, "\n\n".join(plain), cited


def save_answer(db, user, run_id, data):
    # current_user holds the owner's row lock, serializing saves and note mutations.
    run = chat_service.get_run(db, user.id, run_id)
    existing = db.scalar(select(AiSavedNote).where(AiSavedNote.owner_id == user.id, AiSavedNote.run_id == run_id))
    if existing:
        # Never replace a saved note or its edits, including when it is in the recycle bin.
        return metadata(existing, db.get(Note, existing.note_id))
    if run.status != "succeeded" or not run.answer.strip():
        raise AppError(409, "answer_not_complete", "请等待完整回答生成后再保存为笔记")
    source = s.PdfSourceOut.model_validate(run.source) if run.source else None
    body, plain, cited = note_content(run, source)
    try:
        create = ns.NoteCreate(title=data.title, notebook_id=data.notebook_id, body_json=body, plain_text=plain)
    except ValueError:
        raise AppError(422, "answer_note_too_large", "回答内容超过笔记正文限制，暂时无法保存") from None
    note = notes.new_note(db, user, create)
    saved = AiSavedNote(owner_id=user.id, run_id=run.id, note_id=note.id,
                        source=source.model_dump(mode="json") if source else None, cited_page_numbers=cited)
    db.add(saved)
    db.flush()
    result = metadata(saved, note)
    # The note, initial version, and provenance either all commit or all roll back.
    db.commit()
    return result
