from datetime import date, datetime, timezone
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class ActivityEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID
    kind: Literal['reading', 'card_practice', 'quiz', 'review']
    note_id: UUID | None = None
    card_id: UUID | None = None
    quiz_id: UUID | None = None
    started_at: AwareDatetime
    ended_at: AwareDatetime

    @model_validator(mode='after')
    def valid(self):
        try:
            self.started_at = self.started_at.astimezone(timezone.utc)
            self.ended_at = self.ended_at.astimezone(timezone.utc)
        except OverflowError:
            raise ValueError('Activity timestamps must be representable in UTC') from None
        ids = [self.note_id, self.card_id, self.quiz_id]
        expected = {'reading': self.note_id, 'card_practice': self.card_id, 'quiz': self.quiz_id}
        if sum(v is not None for v in ids) != 1 or self.kind != 'review' and expected[self.kind] is None:
            raise ValueError('Activity kind must match exactly one resource')
        if not 0 < (self.ended_at-self.started_at).total_seconds() <= 60:
            raise ValueError('Each activity segment must be greater than zero and at most 60 seconds')
        return self


class ActivityBatchIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    events: list[ActivityEventIn] = Field(min_length=1, max_length=100)


class ActivityBatchOut(BaseModel):
    accepted: int
    duplicates: int


class RatingCounts(BaseModel):
    again: int = 0
    hard: int = 0
    good: int = 0
    easy: int = 0


class QuizStats(BaseModel):
    submitted: int = 0
    graded: int = 0
    needs_review: int = 0
    actual_points: int = 0
    max_points: int = 0
    manually_corrected: int = 0


class StatsDay(BaseModel):
    date: date
    total_seconds: float = 0
    reading_seconds: float = 0
    reading_notes: int = 0
    review_seconds: float = 0
    practice_seconds: float = 0
    quiz_seconds: float = 0
    formal_reviews: int = 0
    distinct_review_objects: int = 0
    practice_count: int = 0
    ratings: RatingCounts = Field(default_factory=RatingCounts)
    quiz: QuizStats = Field(default_factory=QuizStats)


class ReadingProgress(BaseModel):
    recorded_notes: int = 0
    active_notes_read: int = 0
    active_notes_total: int = 0


class StatsOut(BaseModel):
    timezone: str
    date_from: date
    date_to: date
    days: list[StatsDay]
    reading_progress: ReadingProgress


class ResourceCounts(BaseModel):
    note: int = 0
    card: int = 0
    quiz: int = 0


class CalendarDay(BaseModel):
    date: date
    completed: ResourceCounts = Field(default_factory=ResourceCounts)
    practice: ResourceCounts = Field(default_factory=ResourceCounts)
    quiz: QuizStats = Field(default_factory=QuizStats)
    due: ResourceCounts = Field(default_factory=ResourceCounts)


class CalendarOut(BaseModel):
    timezone: str
    date_from: date
    date_to: date
    kind: Literal['note', 'card', 'quiz'] | None
    days: list[CalendarDay]
