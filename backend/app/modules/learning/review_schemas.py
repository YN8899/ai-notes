from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.modules.notes.schemas import validate_pg_text

ReviewKind = Literal['note', 'card', 'quiz']
ReviewStrategy = Literal['manual', 'fsrs']
ReviewStatus = Literal['active', 'paused', 'archived']
Rating = Annotated[int, Field(strict=True, ge=1, le=4)]


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ReviewSource(ReviewInput):
    kind: ReviewKind
    note_id: UUID | None = None
    card_id: UUID | None = None
    quiz_id: UUID | None = None

    @model_validator(mode='after')
    def exact_source(self):
        if any((getattr(self, key + '_id') is not None) != (key == self.kind) for key in ('note', 'card', 'quiz')):
            raise ValueError('Exactly one matching source is required')
        return self


class ReviewTimeInput(ReviewInput):
    next_due_at: datetime | None = None

    @field_validator('next_due_at')
    @classmethod
    def aware(cls, value):
        if value is None or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('Time must include timezone and cannot be null')
        return value.astimezone(timezone.utc)


class ReviewPlanCreate(ReviewSource, ReviewTimeInput):
    request_key: UUID
    strategy: ReviewStrategy = 'manual'


class ReviewPlanPatch(ReviewTimeInput):
    request_key: UUID
    revision: int = Field(strict=True, ge=1)
    operation: Literal['pause', 'resume', 'archive', 'reschedule', 'switch']
    strategy: ReviewStrategy | None = None
    fsrs_mode: Literal['resume_last', 'restart'] | None = None
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator('reason')
    @classmethod
    def reason_text(cls, value):
        return validate_pg_text(value).strip() if value is not None else None

    @model_validator(mode='after')
    def operation_fields(self):
        if self.operation in {'reschedule', 'switch'} and not self.reason:
            raise ValueError('Reason is required')
        if self.operation == 'reschedule' and self.next_due_at is None:
            raise ValueError('Time is required')
        if self.operation == 'switch':
            if self.strategy is None or self.strategy == 'fsrs' and self.fsrs_mode is None:
                raise ValueError('Choose strategy and explicit FSRS resume or restart')
            if self.strategy == 'manual' and self.fsrs_mode is not None:
                raise ValueError('FSRS mode only applies to FSRS')
        elif self.strategy is not None or self.fsrs_mode is not None:
            raise ValueError('Strategy only applies to switching')
        if self.operation not in {'reschedule', 'switch'} and 'next_due_at' in self.model_fields_set:
            raise ValueError('Time only applies to reschedule or switch')
        return self


class ReviewSubmit(ReviewTimeInput):
    request_key: UUID
    revision: int = Field(strict=True, ge=1)
    rating: Rating
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator('reason')
    @classmethod
    def reason_text(cls, value):
        return validate_pg_text(value).strip() if value is not None else None


class ReviewPractice(ReviewSource):
    request_key: UUID
    rating: Rating
    plan_id: UUID | None = None
    revision: int | None = Field(default=None, strict=True, ge=1)

    @model_validator(mode='after')
    def plan_revision(self):
        if (self.plan_id is None) != (self.revision is None):
            raise ValueError('Plan and revision must be supplied together')
        return self


class ReviewItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: ReviewKind
    note_id: UUID | None
    card_id: UUID | None
    quiz_id: UUID | None
    title: str
    eligible: bool


class ReviewPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    item_id: UUID
    item: ReviewItemOut
    strategy: ReviewStrategy
    status: ReviewStatus
    next_due_at: datetime
    algorithm_version: str
    scheduler_state: dict
    last_fsrs_state: dict | None
    revision: int
    created_at: datetime
    updated_at: datetime


class ReviewLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    item_id: UUID
    plan_id: UUID | None
    request_key: UUID
    action: str
    rating: int | None
    scheduled_for: datetime | None
    reviewed_at: datetime
    old_snapshot: dict | None
    new_snapshot: dict | None
    reason: str | None


class ReviewReceipt(BaseModel):
    plan: ReviewPlanOut | None
    log: ReviewLogOut


class ReviewPlanList(BaseModel):
    items: list[ReviewPlanOut]
    total: int
    offset: int
    limit: int


class ReviewLogList(BaseModel):
    items: list[ReviewLogOut]
    total: int
    offset: int
    limit: int
