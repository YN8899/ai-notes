from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.notes.schemas import Input, Revision


class SocialMemberOut(BaseModel):
    id: UUID
    username: str
    display_name: str
    relationship: Literal["self", "friend", "incoming", "outgoing", "none"]
    following: bool


class SocialMemberList(BaseModel):
    items: list[SocialMemberOut]
    next_cursor: str | None
    limit: int


class SocialProfileOut(SocialMemberOut):
    bio: str | None
    subject: str | None
    details: str | None
    visibility: Literal["friends", "private"] | None
    revision: int | None
    details_visible: bool


class SocialProfilePatch(Input):
    revision: Revision
    display_name: str = Field(min_length=1, max_length=64)
    bio: str = Field(default="", max_length=240)
    subject: str = Field(default="", max_length=80)
    details: str = Field(default="", max_length=4000)
    visibility: Literal["friends", "private"] = "friends"


class FriendRequestCreate(Input):
    user_id: UUID


class FriendRequestRespond(Input):
    decision: Literal["accept", "reject"]
    revision: Revision


class FriendRequestOut(BaseModel):
    id: UUID
    member: SocialMemberOut
    requester_id: UUID
    status: Literal["pending", "accepted", "rejected", "removed"]
    revision: int
    created_at: datetime
    updated_at: datetime


class FriendRequestList(BaseModel):
    items: list[FriendRequestOut]
    next_cursor: str | None
    limit: int


class NoteGrantCreate(Input):
    note_id: UUID
    grantee_id: UUID
    note_revision: Revision


class NoteGrantOut(BaseModel):
    id: UUID
    note_id: UUID
    owner: SocialMemberOut
    recipient: SocialMemberOut
    title: str
    kind: Literal["text", "image"]
    source_revision: int
    revision: int
    created_at: datetime
    revoked_at: datetime | None


class NoteGrantList(BaseModel):
    items: list[NoteGrantOut]
    next_cursor: str | None
    limit: int


class SharedPageOut(BaseModel):
    id: UUID
    position: int
    revision: int
    filename: str
    width: int | None
    height: int | None
    status: str
    image_url: str


class SharedNoteOut(BaseModel):
    grant: NoteGrantOut
    body_json: dict
    plain_text: str
    pages: list[SharedPageOut]


class SocialCountsOut(BaseModel):
    friends: int
    incoming_requests: int
    following: int
    received_shares: int
