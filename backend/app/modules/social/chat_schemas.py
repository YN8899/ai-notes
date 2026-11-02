from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.modules.notes.schemas import Input


class ThreadCreate(Input):
    user_id: UUID


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    filename: str
    mime: str
    bytes: int


class MessageCreate(Input):
    client_message_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    body: str = Field(default="", max_length=8000)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def content(self):
        if not self.body.strip() and not self.attachment_ids:
            raise ValueError("message_empty")
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            raise ValueError("duplicate_attachment")
        return self


class MessageOut(BaseModel):
    id: UUID
    thread_id: UUID
    sender_id: UUID
    client_message_id: str
    seq: int
    body: str
    created_at: datetime
    attachments: list[AttachmentOut]


class MessageList(BaseModel):
    items: list[MessageOut]
    next_after: int
    has_more: bool


class ThreadOut(BaseModel):
    id: UUID
    peer_id: UUID
    peer_name: str
    peer_username: str
    can_send: bool
    last_seq: int
    last_read_seq: int
    peer_read_seq: int
    unread: int
    created_at: datetime


class ThreadList(BaseModel):
    items: list[ThreadOut]
    next_cursor: str | None


class ChatSummary(BaseModel):
    threads: int
    unread_messages: int
    unread_threads: int


class ReadIn(Input):
    seq: int = Field(ge=0, le=9223372036854775807)


class ReadOut(BaseModel):
    last_read_seq: int


class RealtimeTicketOut(BaseModel):
    ticket: str
    expires_at: datetime
