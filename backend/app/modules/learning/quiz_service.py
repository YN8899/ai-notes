"""Owner-serialized quiz edits, frozen attempts, durable operation receipts and local scoring."""
from copy import deepcopy
from uuid import UUID
from sqlalchemy import delete, select
from app.modules.learning import quiz_schemas as s, card_sources, review_service
from app.modules.learning.card_service import owned, revision, listing
from app.modules.learning.quiz_models import QuizIdentity, QuizQuestion, QuizAttempt, QuizAnswer, QuizOperation, QuizAnswerReview
from app.modules.learning.card_generation import digest
from app.shared.db import utcnow
from app.shared.errors import AppError


def questions(db, owner, quiz_id):
    return list(db.scalars(select(QuizQuestion).where(QuizQuestion.owner_id == owner, QuizQuestion.quiz_id == quiz_id).order_by(QuizQuestion.position)))


def question_output(db, row):
    valid, error = True, None
    try:
        card_sources.validate(db, row.owner_id, row.source)
    except AppError as exc:
        valid, error = False, exc.code
    return s.QuestionOut(id=row.id, position=row.position,
        **{key: getattr(row, key) for key in s.QuestionContent.model_fields},
        source=card_sources.public(row.source), source_valid=valid, source_error=error)


def output(db, row):
    return s.QuizOut(**s.QuizSummary.model_validate(row).model_dump(),
                     questions=[question_output(db, item) for item in questions(db, row.owner_id, row.id)])


def replace_questions(db, owner, row, contents):
    # Freeze and validate ALL inputs before replacing any existing row.
    frozen = [(item, card_sources.freeze(db, owner, item.source)) for item in contents]
    db.execute(delete(QuizQuestion).where(QuizQuestion.owner_id == owner, QuizQuestion.quiz_id == row.id))
    for position, (item, source) in enumerate(frozen):
        db.add(QuizQuestion(owner_id=owner, quiz_id=row.id, position=position, source=source,
                            **item.model_dump(exclude={"source"})))
    row.question_count = len(contents)
    db.flush()


def create(db, owner, data):
    review_service.lock_owner(db, owner)
    row = QuizIdentity(owner_id=owner, title=data.title)
    db.add(row)
    db.flush()
    if data.questions:
        replace_questions(db, owner, row, data.questions)
    db.commit()
    return row


def validate_confirmed(db, owner, row):
    rows = questions(db, owner, row.id)
    if not 1 <= len(rows) <= 50 or row.question_count != len(rows):
        raise AppError(422, "quiz_questions_required", "确认测验需要1至50道完整题目")
    for item in rows:
        s.QuestionContent.model_validate({key: getattr(item, key) for key in s.QuestionContent.model_fields})
        card_sources.validate(db, owner, item.source)
    return rows


def patch(db, owner, quiz_id, data):
    review_service.lock_owner(db, owner)
    row = owned(db, QuizIdentity, owner, quiz_id, lock=True)
    revision(row, data.revision)
    if "title" in data.model_fields_set:
        row.title = data.title
    if "questions" in data.model_fields_set:
        replace_questions(db, owner, row, data.questions)
        row.status = "draft"
    if "status" in data.model_fields_set:
        row.status = data.status
    if row.status == "confirmed":
        validate_confirmed(db, owner, row)
    row.revision += 1
    row.updated_at = utcnow()
    db.commit()
    return row


def start(db, owner, quiz_id, data):
    review_service.lock_owner(db, owner)
    fingerprint = digest({"action": "start", "quiz_id": str(quiz_id), "body": data.model_dump(mode="json")})
    previous = db.scalar(select(QuizOperation).where(QuizOperation.owner_id == owner, QuizOperation.request_key == data.request_key))
    if previous:
        if previous.request_hash != fingerprint:
            raise AppError(409, "request_key_conflict", "请求标识已用于其他测验或开始参数")
        return s.AttemptOut.model_validate(previous.result)
    quiz = owned(db, QuizIdentity, owner, quiz_id, lock=True)
    revision(quiz, data.revision)
    if quiz.status != "confirmed":
        raise AppError(409, "quiz_not_confirmed", "请先确认测验")
    rows = validate_confirmed(db, owner, quiz)
    snapshot = [{"id": str(item.id), "position": item.position, "source": deepcopy(item.source),
                 **{key: getattr(item, key) for key in s.QuestionContent.model_fields}} for item in rows]
    attempt = QuizAttempt(owner_id=owner, quiz_id=quiz.id, request_key=data.request_key,
                          quiz_revision=quiz.revision, title=quiz.title, snapshot=snapshot,
                          max_points=sum(item.max_points for item in rows))
    db.add(attempt)
    db.flush()
    for item in rows:
        db.add(QuizAnswer(owner_id=owner, attempt_id=attempt.id, question_id=item.id, answer=None))
    db.flush()
    result = attempt_output(db, attempt)
    db.add(QuizOperation(owner_id=owner, attempt_id=attempt.id, request_key=data.request_key, request_hash=fingerprint,
                         result=result.model_dump(mode="json", exclude_unset=True)))
    db.commit()
    return result


def attempt_answers(db, row):
    return list(db.scalars(select(QuizAnswer).where(QuizAnswer.owner_id == row.owner_id, QuizAnswer.attempt_id == row.id).order_by(QuizAnswer.created_at, QuizAnswer.id)))


def attempt_output(db, row):
    exposed = []
    for item in row.snapshot:
        safe = {key: item[key] for key in ("id", "position", "kind", "prompt", "options", "max_points")}
        safe["source"] = card_sources.public(item["source"])
        if row.status != "in_progress":
            safe.update(correct_answer=item["correct_answer"], explanation=item["explanation"])
        exposed.append(s.AttemptQuestion(**safe))
    answers = attempt_answers(db, row)
    history = list(db.scalars(select(QuizAnswerReview).where(QuizAnswerReview.owner_id == row.owner_id,
        QuizAnswerReview.answer_id.in_([a.id for a in answers])).order_by(QuizAnswerReview.created_at, QuizAnswerReview.id)))
    return s.AttemptOut(**{key: getattr(row, key) for key in ("id", "quiz_id", "quiz_revision", "title", "status", "revision", "total_points", "max_points", "created_at", "submitted_at", "finished_at")},
                        questions=exposed, answers=[s.AnswerOut.model_validate(a) for a in answers],
                        reviews=[s.AnswerReviewOut.model_validate(r) for r in history],
                        scoring_policy="exact_choices_and_boolean;fill_trim_casefold_all_or_nothing")


def locked_attempt(db, owner, attempt_id):
    review_service.lock_owner(db, owner)
    return owned(db, QuizAttempt, owner, attempt_id, lock=True)


def question_answer(db, owner, attempt, question_id):
    question = next((q for q in attempt.snapshot if q["id"] == str(question_id)), None)
    answer = db.scalar(select(QuizAnswer).where(QuizAnswer.owner_id == owner, QuizAnswer.attempt_id == attempt.id,
                      QuizAnswer.question_id == question_id).with_for_update().execution_options(populate_existing=True))
    if question is None or answer is None:
        raise AppError(404, "quiz_question_not_found", "作答题目不存在")
    return question, answer


def replay(db, owner, data, action, attempt_id, question_id=None):
    fingerprint = digest({"action": action, "attempt_id": str(attempt_id), "question_id": str(question_id), "body": data.model_dump(mode="json")})
    prior = db.scalar(select(QuizOperation).where(QuizOperation.owner_id == owner, QuizOperation.request_key == data.request_key))
    if prior:
        if prior.request_hash != fingerprint:
            raise AppError(409, "request_key_conflict", "相同请求标识已用于不同操作")
        return fingerprint, s.AttemptOut.model_validate(prior.result)
    return fingerprint, None


def finish_operation(db, owner, row, data, fingerprint):
    db.flush()
    result = attempt_output(db, row)
    db.add(QuizOperation(owner_id=owner, attempt_id=row.id, request_key=data.request_key, request_hash=fingerprint,
                         result=result.model_dump(mode="json", exclude_unset=True)))
    db.commit()
    return result


def student_text(value):
    try:
        return s.validate_pg_text(value)
    except ValueError:
        raise AppError(422, "quiz_answer_invalid", "答案文本含无效字符") from None


def valid_student(question, value):
    if value is None:
        return
    kind = question["kind"]
    if kind in ("single_choice", "multiple_choice"):
        indices = [value] if kind == "single_choice" else value
        if not isinstance(indices, list) or len(indices) > len(question["options"]) or any(type(v) is not int or not 0 <= v < len(question["options"]) for v in indices) or len(set(indices)) != len(indices):
            raise AppError(422, "quiz_answer_invalid", "选择题答案必须是有效的不重复选项索引")
    elif kind == "true_false" and type(value) is not bool:
        raise AppError(422, "quiz_answer_invalid", "判断题答案必须为布尔值")
    elif kind == "fill_blank":
        if not isinstance(value, list) or len(value) != len(question["correct_answer"]) or any(type(v) is not str or len(v) > 2000 for v in value):
            raise AppError(422, "quiz_answer_invalid", "填空答案必须与空位逐一对应")
        for v in value:
            student_text(v)
    elif kind == "short_answer":
        if type(value) is not str or len(value) > 8000:
            raise AppError(422, "quiz_answer_invalid", "简答题答案必须为8000字符内文本")
        student_text(value)


def save_answer(db, owner, attempt_id, question_id, data):
    row = locked_attempt(db, owner, attempt_id)
    fingerprint, prior = replay(db, owner, data, "answer", row.id, question_id)
    if prior:
        return prior
    revision(row, data.revision)
    if row.status != "in_progress":
        raise AppError(409, "quiz_attempt_closed", "测验已交卷，不能修改答案")
    question, answer = question_answer(db, owner, row, question_id)
    valid_student(question, data.answer)
    answer.answer = deepcopy(data.answer)
    answer.revision += 1
    row.revision += 1
    return finish_operation(db, owner, row, data, fingerprint)


def local_grade(question, answer):
    value, expected, kind = answer.answer, question["correct_answer"], question["kind"]
    unanswered = value is None or value == "" or value == [] or isinstance(value, list) and all(type(v) is str and not v.strip() for v in value) or type(value) is str and not value.strip()
    if unanswered:
        answer.status, answer.points, answer.feedback = "graded", 0, "未作答"
    elif kind == "short_answer":
        answer.status, answer.points, answer.feedback = "needs_review", None, "简答题等待人工复核或显式AI评估"
    else:
        if kind == "multiple_choice":
            correct = set(value) == set(expected)
        elif kind == "fill_blank":
            correct = [v.strip().casefold() for v in value] == [v.strip().casefold() for v in expected]
        else:
            correct = type(value) is type(expected) and value == expected
        answer.status, answer.points, answer.feedback = "graded", question["max_points"] if correct else 0, ""
    answer.provenance = "local"
    answer.revision += 1


def recalculate(db, row):
    db.flush()
    answers = attempt_answers(db, row)
    row.total_points = sum(a.points or 0 for a in answers)
    row.status = "needs_review" if any(a.status != "graded" for a in answers) else "graded"
    row.finished_at = utcnow() if row.status == "graded" else None


def submit(db, owner, attempt_id, data):
    row = locked_attempt(db, owner, attempt_id)
    fingerprint, prior = replay(db, owner, data, "submit", row.id)
    if prior:
        return prior
    revision(row, data.revision)
    if row.status != "in_progress":
        raise AppError(409, "quiz_attempt_closed", "测验已经交卷")
    for question in row.snapshot:
        _, answer = question_answer(db, owner, row, UUID(question["id"]))
        local_grade(question, answer)
    row.submitted_at = utcnow()
    row.revision += 1
    recalculate(db, row)
    return finish_operation(db, owner, row, data, fingerprint)


def grade_snapshot(answer):
    return {key: getattr(answer, key) for key in ("revision", "status", "points", "feedback", "provenance")}


def review(db, owner, attempt_id, question_id, data):
    row = locked_attempt(db, owner, attempt_id)
    fingerprint, prior = replay(db, owner, data, "review", row.id, question_id)
    if prior:
        return prior
    revision(row, data.revision)
    if row.status == "in_progress":
        raise AppError(409, "quiz_not_submitted", "交卷后才可复核")
    question, answer = question_answer(db, owner, row, question_id)
    if data.points > question["max_points"]:
        raise AppError(422, "quiz_score_out_of_range", "分数超过该题满分")
    before = grade_snapshot(answer)
    answer.status, answer.points, answer.feedback, answer.provenance = "graded", data.points, data.feedback, "manual"
    answer.revision += 1
    db.add(QuizAnswerReview(owner_id=owner, answer_id=answer.id, old_grade=before, new_grade=grade_snapshot(answer), reason=data.reason, request_key=data.request_key))
    row.revision += 1
    recalculate(db, row)
    return finish_operation(db, owner, row, data, fingerprint)
