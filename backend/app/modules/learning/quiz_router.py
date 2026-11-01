from uuid import UUID
from fastapi import APIRouter, Query
from app.modules.learning import quiz_schemas as s, quiz_service as service, card_sources, quiz_generation as generation, quiz_grading as grading
from app.modules.learning.quiz_models import QuizIdentity, QuizQuestion, QuizAttempt, QuizGeneration, QuizGrading
from app.shared.dependencies import DB, CurrentUser

router = APIRouter(prefix="/learning", tags=["learning-quizzes"])


@router.get("/quizzes", response_model=s.QuizList)
def quizzes(db: DB, user: CurrentUser, status: s.Status | None = None,
            offset: int = Query(default=0, ge=0, le=100000), limit: int = Query(default=50, ge=1, le=50)):
    rows, total = service.listing(db, user.id, QuizIdentity, offset, limit, status=status)
    return dict(items=rows, total=total, offset=offset, limit=limit)


@router.post("/quizzes", response_model=s.QuizOut, status_code=201)
def create(data: s.QuizCreate, db: DB, user: CurrentUser):
    return service.output(db, service.create(db, user.id, data))


@router.get("/quizzes/{quiz_id}", response_model=s.QuizOut)
def get(quiz_id: UUID, db: DB, user: CurrentUser):
    return service.output(db, service.owned(db, QuizIdentity, user.id, quiz_id))


@router.patch("/quizzes/{quiz_id}", response_model=s.QuizOut)
def patch(quiz_id: UUID, data: s.QuizPatch, db: DB, user: CurrentUser):
    return service.output(db, service.patch(db, user.id, quiz_id, data))


@router.put("/quizzes/{quiz_id}/questions", response_model=s.QuizOut)
def replace(quiz_id: UUID, data: s.QuestionBatch, db: DB, user: CurrentUser):
    return service.output(db, service.patch(db, user.id, quiz_id, s.QuizPatch(**data.model_dump())))


@router.get("/quizzes/{quiz_id}/questions/{question_id}/source", response_model=s.SourceRead)
def source(quiz_id: UUID, question_id: UUID, db: DB, user: CurrentUser):
    service.owned(db, QuizIdentity, user.id, quiz_id)
    row = service.owned(db, QuizQuestion, user.id, question_id)
    if row.quiz_id != quiz_id:
        from app.shared.errors import AppError
        raise AppError(404, "quiz_question_not_found", "题目不存在")
    return card_sources.read(db, user.id, row.source)


@router.post("/quizzes/{quiz_id}/attempts", response_model=s.AttemptOut, response_model_exclude_unset=True, status_code=201)
def start(quiz_id: UUID, data: s.AttemptCreate, db: DB, user: CurrentUser):
    return service.start(db, user.id, quiz_id, data)


@router.get("/quiz-attempts", response_model=s.AttemptList, response_model_exclude_unset=True)
def attempts(db: DB, user: CurrentUser, quiz_id: UUID | None = None,
             offset: int = Query(default=0, ge=0, le=100000), limit: int = Query(default=50, ge=1, le=50)):
    rows, total = service.listing(db, user.id, QuizAttempt, offset, limit, quiz_id=quiz_id)
    return dict(items=[service.attempt_output(db, row) for row in rows], total=total, offset=offset, limit=limit)


@router.get("/quiz-attempts/{attempt_id}", response_model=s.AttemptOut, response_model_exclude_unset=True)
def attempt(attempt_id: UUID, db: DB, user: CurrentUser):
    return service.attempt_output(db, service.owned(db, QuizAttempt, user.id, attempt_id))


@router.get("/quiz-attempts/{attempt_id}/questions/{question_id}/source", response_model=s.SourceRead)
def attempt_source(attempt_id: UUID, question_id: UUID, db: DB, user: CurrentUser):
    row = service.owned(db, QuizAttempt, user.id, attempt_id)
    question, _ = service.question_answer(db, user.id, row, question_id)
    return card_sources.read(db, user.id, question["source"])


@router.put("/quiz-attempts/{attempt_id}/answers/{question_id}", response_model=s.AttemptOut, response_model_exclude_unset=True)
def answer(attempt_id: UUID, question_id: UUID, data: s.AnswerSave, db: DB, user: CurrentUser):
    return service.save_answer(db, user.id, attempt_id, question_id, data)


@router.post("/quiz-attempts/{attempt_id}/submit", response_model=s.AttemptOut, response_model_exclude_unset=True)
def submit(attempt_id: UUID, data: s.Operation, db: DB, user: CurrentUser):
    return service.submit(db, user.id, attempt_id, data)


@router.post("/quiz-attempts/{attempt_id}/answers/{question_id}/reviews", response_model=s.AttemptOut, response_model_exclude_unset=True)
def review(attempt_id: UUID, question_id: UUID, data: s.ManualReview, db: DB, user: CurrentUser):
    return service.review(db, user.id, attempt_id, question_id, data)


@router.post("/quiz-generations/preview", response_model=s.GenerationPreviewOut)
def generation_preview(data: s.GenerationPreview, db: DB, user: CurrentUser):
    return generation.preview(db, user.id, data)


@router.post("/quiz-generations", response_model=s.GenerationOut, status_code=201)
def generation_create(data: s.GenerationCreate, db: DB, user: CurrentUser):
    return generation.output(db, generation.create(db, user.id, data))


@router.get("/quiz-generations", response_model=s.GenerationList)
def generations(db: DB, user: CurrentUser, offset: int = Query(default=0, ge=0, le=100000), limit: int = Query(default=50, ge=1, le=50)):
    rows, total = service.listing(db, user.id, QuizGeneration, offset, limit)
    rows = [generation.materialize(db, row.id) for row in rows]
    db.commit()
    return dict(items=[generation.output(db, row) for row in rows], total=total, offset=offset, limit=limit)


@router.get("/quiz-generations/{generation_id}", response_model=s.GenerationOut)
def generation_get(generation_id: UUID, db: DB, user: CurrentUser):
    row = service.owned(db, QuizGeneration, user.id, generation_id)
    row = generation.materialize(db, row.id)
    db.commit()
    return generation.output(db, row)


@router.post("/quiz-gradings/preview", response_model=s.GradingPreviewOut)
def grading_preview(data: s.GradingPreview, db: DB, user: CurrentUser):
    return grading.preview(db, user.id, data)


@router.post("/quiz-gradings", response_model=s.GradingOut, status_code=201)
def grading_create(data: s.GradingCreate, db: DB, user: CurrentUser):
    return grading.output(db, grading.create(db, user.id, data))


@router.get("/quiz-gradings/{grading_id}", response_model=s.GradingOut)
def grading_get(grading_id: UUID, db: DB, user: CurrentUser):
    row = service.owned(db, QuizGrading, user.id, grading_id)
    row = grading.materialize(db, row.id)
    db.commit()
    return grading.output(db, row)
