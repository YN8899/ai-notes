"""Validate detached v3 learning rows before an empty restore target is opened."""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID
from pydantic import BaseModel
from app.modules.learning import card_schemas, quiz_schemas, review_schemas, reminder_schemas, scheduler


def prepare(rows, *, restore_source, source_context):
    """Keep stable row/receipt UUIDs; account recovery always targets an empty DB."""
    def require(condition):
        if not condition:
            raise ValueError("invalid_learning_archive")

    def no_dropped_fields(original, validated):
        # DTO validation accepts equivalent RFC3339 offsets. SQL migration
        # backfills use +00:00 while Pydantic renders UTC as Z; neither spelling
        # changes the fact. Reject extra fields structurally, not by comparing
        # JSON date spellings or rewriting immutable historical JSON.
        if isinstance(validated,BaseModel):
            require(isinstance(original,dict) and set(original)==validated.model_fields_set)
            for name in original:
                no_dropped_fields(original[name],getattr(validated,name))
        elif isinstance(validated,list):
            require(isinstance(original,list) and len(original)==len(validated))
            for raw,parsed in zip(original,validated,strict=True):
                no_dropped_fields(raw,parsed)
        elif isinstance(validated,(UUID,datetime)):
            require(isinstance(original,str))
            if isinstance(validated,UUID):
                require(str(validated)==original)
            else:
                require(datetime.fromisoformat(original)==validated)
        elif isinstance(validated,(bool,int,str)):
            require(type(original) is type(validated) and original==validated)
        elif isinstance(validated,float):
            require(type(original) in {int,float} and original==validated)
        elif validated is None:
            require(original is None)

    def source(value):
        if value is None:
            return None
        if value['kind'] == 'text':
            return restore_source(value, internal=True, **source_context)
        frozen = deepcopy(value)
        identity = frozen.pop('_card_identity')
        result = restore_source(frozen, internal=True, historical=True, **source_context)
        result['_card_identity'] = identity
        return result

    def state(value):
        now = datetime.now(timezone.utc)
        if value['strategy'] == 'manual':
            require(value['algorithm_version'] == scheduler.MANUAL_VERSION and value['scheduler_state']=={})
        else:
            require(value['strategy']=='fsrs' and value['algorithm_version']==scheduler.FSRS_VERSION)
            scheduler._card(value['scheduler_state'], now)
        old = value.get('last_fsrs_state')
        if old is not None:
            require(isinstance(old,dict) and set(old)=={'algorithm_version','scheduler_state'}
                    and old['algorithm_version']==scheduler.FSRS_VERSION)
            scheduler._card(old['scheduler_state'], now)

    for table in ('learning_cards','card_generations','quiz_questions','quiz_generations'):
        for row in rows[table]:
            row['source'] = source(row['source'])
    for row in rows['learning_decks']:
        card_schemas.DeckCreate(name=row['name'])
        require(row['status'] in {'active','archived'})
    for row in rows['learning_cards']:
        content = card_schemas.CardContent.model_validate({key:row[key] for key in card_schemas.CardContent.model_fields})
        from app.modules.learning.card_sources import assert_masks
        assert_masks(content,row['source'])
        require(row['status'] in {'draft','confirmed','archived'})
        require((row['generation_id'] is None)==(row['generation_ordinal'] is None))
    for row in rows['quiz_questions']:
        quiz_schemas.QuestionContent.model_validate({key:row[key] for key in quiz_schemas.QuestionContent.model_fields})
    for row in rows['learning_quizzes']:
        quiz_schemas.QuizCreate(title=row['title'])
        require(row['status'] in {'draft','confirmed','archived'})
        count = sum(1 for question in rows['quiz_questions'] if question['quiz_id']==row['id'])
        require(count == row['question_count'] and count<=50 and (row['status']!='confirmed' or count>0))
    snapshots = {}
    for row in rows['quiz_attempts']:
        require(row['status'] in {'in_progress','submitted','needs_review','graded'})
        for question in row['snapshot']:
            require(set(question)=={'id','position','kind','prompt','options','correct_answer','explanation','max_points','source'})
            quiz_schemas.QuestionContent.model_validate({key:question[key] for key in quiz_schemas.QuestionContent.model_fields})
            question['source'] = source(question['source'])
        require(sum(q['max_points'] for q in row['snapshot'])==row['max_points']
                and 0 <= row['total_points'] <= row['max_points'])
        snapshots[row['id']] = {q['id']:q for q in row['snapshot']}
    for row in rows['quiz_answers']:
        require(str(row['question_id']) in snapshots[row['attempt_id']])
        require(row['status'] in {'ungraded','needs_review','graded'})
        question = snapshots[row['attempt_id']][str(row['question_id'])]
        require(row['points'] is None or 0 <= row['points'] <= question['max_points'])
        from app.modules.learning.quiz_service import valid_student
        valid_student(question,row['answer'])
    for row in rows['quiz_operations']:
        output = quiz_schemas.AttemptOut.model_validate(row['result'])
        require(output.id==row['attempt_id'])
        # Strict comparison detects fields silently ignored by DTO parsing.
        no_dropped_fields(row['result'],output)
    for table in ('card_generations','quiz_generations','quiz_gradings'):
        for row in rows[table]:
            require(row['status'] in {'succeeded','failed'})
    for row in rows['quiz_gradings']:
        require(set(row['input'])=={'question','reference_answer','rubric','max_points','student_answer'})
        if row['result'] is not None:
            quiz_schemas.GradingResult.model_validate(row['result'])
    for row in rows['review_plans']:
        state(row)
        require(row['status'] in {'active','paused','archived'})
    for row in rows['review_logs']:
        for key in ('old_snapshot','new_snapshot'):
            if row[key] is not None:
                state(row[key])
        if row['result_plan'] is not None:
            plan = review_schemas.ReviewPlanOut.model_validate(row['result_plan'])
            require(plan.id==row['plan_id'] and plan.item_id==row['item_id'])
            no_dropped_fields(row['result_plan'],plan)
    for row in rows['user_learning_settings']:
        reminder_schemas.LearningSettingsPatch(revision=row['revision'], timezone=row['timezone'],
            review_time=row['review_time'], reminder_enabled=row['reminder_enabled'])
    for row in rows['daily_review_digests']:
        # The original system Job/outbox are intentionally not runnable after
        # account recovery; the daily ledger still prevents historical replay.
        row['job_id'] = None
