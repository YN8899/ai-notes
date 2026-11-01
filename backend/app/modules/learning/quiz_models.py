"""Quiz identity foundation; question content and confirmation belong to T21."""
import uuid
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.shared.db import Base, utcnow
from app.shared.models import Entity


class QuizIdentity(Entity, Base):
    __tablename__ = "learning_quizzes"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="draft")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    question_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_learning_quiz_owner"),
        CheckConstraint("revision > 0 AND question_count >= 0 AND status IN ('draft','confirmed','archived')", name="learning_quiz_state"),
        CheckConstraint("length(title) BETWEEN 1 AND 200", name="learning_quiz_title"),
    )

from sqlalchemy.dialects.postgresql import JSONB


class QuizQuestion(Entity, Base):
    __tablename__ = "quiz_questions"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    quiz_id: Mapped[uuid.UUID] = mapped_column()
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(24))
    prompt: Mapped[str] = mapped_column(Text)
    options: Mapped[list | None] = mapped_column(JSONB)
    correct_answer: Mapped[object] = mapped_column(JSONB)
    explanation: Mapped[str] = mapped_column(Text, default="")
    max_points: Mapped[int] = mapped_column(Integer)
    source: Mapped[dict | None] = mapped_column(JSONB)
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_quiz_question_owner"),
        UniqueConstraint("quiz_id", "position", name="uq_quiz_question_position"),
        ForeignKeyConstraint(["owner_id", "quiz_id"], ["learning_quizzes.owner_id", "learning_quizzes.id"], name="fk_quiz_question_owner"),
        CheckConstraint("position BETWEEN 0 AND 49 AND max_points BETWEEN 1 AND 100", name="quiz_question_bounds"),
        CheckConstraint("kind IN ('single_choice','multiple_choice','true_false','fill_blank','short_answer')", name="quiz_question_kind"),
        CheckConstraint("length(prompt) BETWEEN 1 AND 8000 AND length(explanation) <= 8000", name="quiz_question_text"))


class QuizAttempt(Entity, Base):
    __tablename__ = "quiz_attempts"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    quiz_id: Mapped[uuid.UUID] = mapped_column()
    request_key: Mapped[uuid.UUID] = mapped_column()
    quiz_revision: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    snapshot: Mapped[list] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="in_progress")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    total_points: Mapped[int] = mapped_column(Integer, default=0)
    max_points: Mapped[int] = mapped_column(Integer)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_quiz_attempt_owner"),
        UniqueConstraint("owner_id", "request_key", name="uq_quiz_attempt_request"),
        ForeignKeyConstraint(["owner_id", "quiz_id"], ["learning_quizzes.owner_id", "learning_quizzes.id"], name="fk_quiz_attempt_quiz_owner"),
        CheckConstraint("revision > 0 AND total_points >= 0 AND total_points <= max_points AND max_points BETWEEN 1 AND 5000", name="quiz_attempt_points"),
        CheckConstraint("status IN ('in_progress','submitted','needs_review','graded')", name="quiz_attempt_state"),
        Index("ix_quiz_attempts_owner", "owner_id", "quiz_id", "created_at"))


class QuizAnswer(Entity, Base):
    __tablename__ = "quiz_answers"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    attempt_id: Mapped[uuid.UUID] = mapped_column()
    question_id: Mapped[uuid.UUID] = mapped_column()
    answer: Mapped[object | None] = mapped_column(JSONB, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="ungraded")
    points: Mapped[int | None] = mapped_column(Integer)
    feedback: Mapped[str] = mapped_column(Text, default="")
    provenance: Mapped[str | None] = mapped_column(String(24))
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_quiz_answer_owner"),
        UniqueConstraint("attempt_id", "question_id", name="uq_quiz_answer_question"),
        ForeignKeyConstraint(["owner_id", "attempt_id"], ["quiz_attempts.owner_id", "quiz_attempts.id"], name="fk_quiz_answer_attempt_owner"),
        CheckConstraint("revision > 0 AND (points IS NULL OR points BETWEEN 0 AND 100)", name="quiz_answer_points"),
        CheckConstraint("status IN ('ungraded','needs_review','graded')", name="quiz_answer_state"))


class QuizOperation(Entity, Base):
    __tablename__ = "quiz_operations"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    attempt_id: Mapped[uuid.UUID] = mapped_column()
    request_key: Mapped[uuid.UUID] = mapped_column()
    request_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        UniqueConstraint("owner_id", "request_key", name="uq_quiz_operation_request"),
        ForeignKeyConstraint(["owner_id", "attempt_id"], ["quiz_attempts.owner_id", "quiz_attempts.id"], name="fk_quiz_operation_attempt_owner"))


class QuizAnswerReview(Entity, Base):
    __tablename__ = "quiz_answer_reviews"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    answer_id: Mapped[uuid.UUID] = mapped_column()
    old_grade: Mapped[dict] = mapped_column(JSONB)
    new_grade: Mapped[dict] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(Text)
    request_key: Mapped[uuid.UUID] = mapped_column()
    __table_args__ = (
        UniqueConstraint("owner_id", "request_key", name="uq_quiz_review_request"),
        ForeignKeyConstraint(["owner_id", "answer_id"], ["quiz_answers.owner_id", "quiz_answers.id"], name="fk_quiz_review_answer_owner"))


class QuizGeneration(Entity, Base):
    __tablename__ = "quiz_generations"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    quiz_id: Mapped[uuid.UUID | None] = mapped_column()
    run_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    request_key: Mapped[uuid.UUID] = mapped_column()
    request_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    kinds: Mapped[list] = mapped_column(JSONB)
    count: Mapped[int] = mapped_column(Integer)
    source: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error_code: Mapped[str | None] = mapped_column(String(64))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("owner_id", "request_key", name="uq_quiz_generation_request"),
        ForeignKeyConstraint(["owner_id", "quiz_id"], ["learning_quizzes.owner_id", "learning_quizzes.id"], name="fk_quiz_generation_quiz_owner"),
        ForeignKeyConstraint(["owner_id", "run_id"], ["ai_runs.owner_id", "ai_runs.id"], name="fk_quiz_generation_run_owner"),
        CheckConstraint("count BETWEEN 1 AND 20 AND status IN ('pending','succeeded','failed')", name="quiz_generation_state"))


class QuizGrading(Entity, Base):
    __tablename__ = "quiz_gradings"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    answer_id: Mapped[uuid.UUID] = mapped_column()
    answer_revision: Mapped[int] = mapped_column(Integer)
    attempt_revision: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    request_key: Mapped[uuid.UUID] = mapped_column()
    request_hash: Mapped[str] = mapped_column(String(64))
    input: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    result: Mapped[dict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(64))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("owner_id", "request_key", name="uq_quiz_grading_request"),
        ForeignKeyConstraint(["owner_id", "answer_id"], ["quiz_answers.owner_id", "quiz_answers.id"], name="fk_quiz_grading_answer_owner"),
        ForeignKeyConstraint(["owner_id", "run_id"], ["ai_runs.owner_id", "ai_runs.id"], name="fk_quiz_grading_run_owner"),
        CheckConstraint("answer_revision > 0 AND attempt_revision > 0 AND status IN ('pending','succeeded','failed')", name="quiz_grading_state"))
