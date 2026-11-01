"""Strict quiz contracts; author DTO and attempt DTO have separate trust boundaries."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.modules.learning.card_schemas import Selection, PublicSource, SourceRead, Input
from app.modules.notes.schemas import validate_pg_text

Kind = Literal["single_choice", "multiple_choice", "true_false", "fill_blank", "short_answer"]
Status = Literal["draft", "confirmed", "archived"]


class QuestionContent(Input):
    kind: Kind
    prompt: str = Field(min_length=1, max_length=8000)
    options: list[str] | None = Field(default=None, min_length=2, max_length=8)
    correct_answer: Any
    explanation: str = Field(default="", max_length=8000)
    max_points: int = Field(default=1, strict=True, ge=1, le=100)

    @field_validator("prompt", "explanation")
    @classmethod
    def valid_text(cls, value):
        return validate_pg_text(value)

    @model_validator(mode="after")
    def content(self):
        if not self.prompt.strip():
            raise ValueError("Empty question")
        answer, options = self.correct_answer, self.options
        if self.kind in ("single_choice", "multiple_choice"):
            if not options or len({v.strip() for v in options}) != len(options) or any(not v.strip() or len(v) > 2000 for v in options):
                raise ValueError("Invalid choices")
            for v in options:
                validate_pg_text(v)
            indices = [answer] if self.kind == "single_choice" else answer
            if not isinstance(indices, list) or not indices or any(type(v) is not int or not 0 <= v < len(options) for v in indices) or len(set(indices)) != len(indices):
                raise ValueError("Invalid correct indices")
        elif options is not None:
            raise ValueError("Options apply to choices only")
        if self.kind == "true_false" and type(answer) is not bool:
            raise ValueError("Boolean required")
        if self.kind == "fill_blank":
            count = self.prompt.count("{{blank}}")
            if not 1 <= count <= 20 or not isinstance(answer, list) or len(answer) != count or any(type(v) is not str or not v.strip() or len(v) > 2000 for v in answer):
                raise ValueError("One nonempty answer per blank required")
            for v in answer:
                validate_pg_text(v)
        if self.kind == "short_answer":
            if type(answer) is not str or not answer.strip() or len(answer) > 8000:
                raise ValueError("Reference answer required")
            validate_pg_text(answer)
        return self


class QuestionCreate(QuestionContent):
    source: Selection | None = None


class QuizCreate(Input):
    title: str = Field(min_length=1, max_length=200)
    questions: list[QuestionCreate] = Field(default_factory=list, max_length=50)

    @field_validator("title")
    @classmethod
    def title_text(cls, value):
        validate_pg_text(value)
        if not value.strip():
            raise ValueError("Empty title")
        return value.strip()


class QuizPatch(Input):
    revision: int = Field(strict=True, ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    status: Status | None = None
    questions: list[QuestionCreate] | None = Field(default=None, min_length=1, max_length=50)

    @model_validator(mode="after")
    def present(self):
        if any(getattr(self, key) is None for key in self.model_fields_set - {"revision"}):
            raise ValueError("Null edits forbidden")
        if self.title is not None:
            self.title = QuizCreate(title=self.title).title
        return self


class QuestionBatch(Input):
    revision: int = Field(strict=True, ge=1)
    questions: list[QuestionCreate] = Field(min_length=1, max_length=50)


class QuestionOut(QuestionContent):
    id: UUID
    position: int
    source: PublicSource | None
    source_valid: bool
    source_error: str | None


class QuizSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    status: Status
    revision: int
    question_count: int
    created_at: datetime
    updated_at: datetime


class QuizOut(QuizSummary):
    questions: list[QuestionOut]


class QuizList(BaseModel):
    items: list[QuizSummary]
    total: int
    offset: int
    limit: int


class AttemptCreate(Input):
    revision: int = Field(strict=True, ge=1)
    request_key: UUID


class Operation(Input):
    revision: int = Field(strict=True, ge=1)
    request_key: UUID


class AnswerSave(Operation):
    answer: Any


class ManualReview(Operation):
    points: int = Field(strict=True, ge=0, le=100)
    reason: str = Field(min_length=1, max_length=2000)
    feedback: str = Field(default="", max_length=8000)

    @model_validator(mode="after")
    def text(self):
        if not self.reason.strip():
            raise ValueError("Review reason required")
        validate_pg_text(self.reason)
        validate_pg_text(self.feedback)
        return self


class AttemptQuestion(BaseModel):
    id: UUID
    position: int
    kind: Kind
    prompt: str
    options: list[str] | None
    max_points: int
    source: PublicSource | None
    # Fields are omitted entirely while an attempt is in progress.
    correct_answer: Any = None
    explanation: str | None = None


class AnswerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    question_id: UUID
    answer: Any
    revision: int
    status: str
    points: int | None
    feedback: str
    provenance: str | None


class AnswerReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    answer_id: UUID
    old_grade: dict
    new_grade: dict
    reason: str
    request_key: UUID
    created_at: datetime


class AttemptOut(BaseModel):
    id: UUID
    quiz_id: UUID
    quiz_revision: int
    title: str
    status: Literal["in_progress", "submitted", "needs_review", "graded"]
    revision: int
    total_points: int
    max_points: int
    questions: list[AttemptQuestion]
    answers: list[AnswerOut]
    reviews: list[AnswerReviewOut]
    created_at: datetime
    submitted_at: datetime | None
    finished_at: datetime | None
    scoring_policy: Literal["exact_choices_and_boolean;fill_trim_casefold_all_or_nothing"] = "exact_choices_and_boolean;fill_trim_casefold_all_or_nothing"


class AttemptList(BaseModel):
    items: list[AttemptOut]
    total: int
    offset: int
    limit: int


class GenerationPreview(QuizCreate):
    # No user-supplied questions on AI requests.
    questions: list[QuestionCreate] = Field(default_factory=list, max_length=0)
    kinds: list[Kind] = Field(min_length=1, max_length=5)
    count: int = Field(strict=True, ge=1, le=20)
    source: Selection
    model: Literal["deepseek-flash", "deepseek-v4-pro"] = "deepseek-flash"

    @model_validator(mode="after")
    def requested_kinds(self):
        if len(set(self.kinds)) != len(self.kinds) or self.count < len(self.kinds):
            raise ValueError("Every distinct requested kind needs at least one question")
        return self


class ConfirmExternal(Input):
    request_key: UUID
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_external: Literal[True]

    @field_validator("confirm_external", mode="before")
    @classmethod
    def explicit(cls, value):
        if value is not True:
            raise ValueError("Explicit external call confirmation required")
        return value


class GenerationCreate(GenerationPreview, ConfirmExternal):
    pass


class GenerationPreviewOut(BaseModel):
    preview_hash: str
    title: str
    source: PublicSource
    kinds: list[Kind]
    count: int
    model: str
    planned_calls: Literal[1] = 1


class GenerationOut(BaseModel):
    id: UUID
    quiz_id: UUID | None
    request_key: UUID
    kinds: list[Kind]
    count: int
    model: str
    status: Literal["pending", "succeeded", "failed"]
    run_status: str
    usage: dict | None
    error_code: str | None
    source: PublicSource
    created_at: datetime
    finished_at: datetime | None


class GenerationList(BaseModel):
    items: list[GenerationOut]
    total: int
    offset: int
    limit: int


class GradingPreview(Input):
    attempt_id: UUID
    question_id: UUID
    revision: int = Field(strict=True, ge=1)
    model: Literal["deepseek-flash", "deepseek-v4-pro"] = "deepseek-flash"


class GradingCreate(GradingPreview, ConfirmExternal):
    pass


class GradingPreviewOut(BaseModel):
    preview_hash: str
    attempt_id: UUID
    question_id: UUID
    revision: int
    answer_revision: int
    model: str
    input: dict
    planned_calls: Literal[1] = 1


class GradingResult(Input):
    score: int = Field(strict=True, ge=0, le=100)
    feedback: str = Field(max_length=8000)
    needs_review: bool = Field(strict=True)

    @field_validator("feedback")
    @classmethod
    def safe_feedback(cls, value):
        return validate_pg_text(value)


class GradingOut(BaseModel):
    id: UUID
    attempt_id: UUID
    question_id: UUID
    request_key: UUID
    answer_revision: int
    attempt_revision: int
    model: str
    status: Literal["pending", "succeeded", "failed"]
    run_status: str
    usage: dict | None
    result: GradingResult | None
    error_code: str | None
    created_at: datetime
    finished_at: datetime | None
