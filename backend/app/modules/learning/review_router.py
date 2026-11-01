from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Query
from app.modules.learning import review_schemas as s, review_service as service, scheduler
from app.modules.learning.review_models import ReviewPlan
from app.shared.dependencies import DB, CurrentUser
from app.shared.errors import AppError

router = APIRouter(prefix='/learning', tags=['learning-reviews'])


@router.post('/review-plans', response_model=s.ReviewReceipt, status_code=201)
def create(data: s.ReviewPlanCreate, db: DB, user: CurrentUser):
    return service.create(db, user.id, data)


def filters(due_from, due_to):
    low = scheduler.utc(due_from) if due_from else None
    high = scheduler.utc(due_to) if due_to else None
    if low and high and low > high:
        raise AppError(422, 'schedule_time_invalid', '开始时间不能晚于结束时间')
    return dict(due_from=low, due_to=high)


@router.get('/review-plans', response_model=s.ReviewPlanList)
def plans(db: DB, user: CurrentUser, kind: s.ReviewKind | None = None, status: s.ReviewStatus | None = None,
          due_from: datetime | None = None, due_to: datetime | None = None, eligible_only: bool = False,
          offset: int = Query(0, ge=0, le=100000), limit: int = Query(50, ge=1, le=50)):
    return service.listing(db, user.id, kind=kind, status=status, eligible_only=eligible_only,
                           offset=offset, limit=limit, **filters(due_from, due_to))


@router.get('/review-due', response_model=s.ReviewPlanList)
def due(db: DB, user: CurrentUser, kind: s.ReviewKind | None = None, due_from: datetime | None = None,
        due_to: datetime | None = None, offset: int = Query(0, ge=0, le=100000), limit: int = Query(50, ge=1, le=50)):
    return service.listing(db, user.id, kind=kind, due_only=True, offset=offset, limit=limit, **filters(due_from, due_to))


@router.get('/review-plans/{plan_id}', response_model=s.ReviewPlanOut)
def plan(plan_id: UUID, db: DB, user: CurrentUser):
    return service.output(db, service.owned(db, ReviewPlan, user.id, plan_id))


@router.patch('/review-plans/{plan_id}', response_model=s.ReviewReceipt)
def patch(plan_id: UUID, data: s.ReviewPlanPatch, db: DB, user: CurrentUser):
    return service.patch(db, user.id, plan_id, data)


@router.post('/review-plans/{plan_id}/reviews', response_model=s.ReviewReceipt)
def review(plan_id: UUID, data: s.ReviewSubmit, db: DB, user: CurrentUser):
    return service.review(db, user.id, plan_id, data)


@router.post('/review-items/practice', response_model=s.ReviewReceipt)
def practice(data: s.ReviewPractice, db: DB, user: CurrentUser):
    return service.practice(db, user.id, data)


@router.get('/review-logs', response_model=s.ReviewLogList)
def logs(db: DB, user: CurrentUser, plan_id: UUID | None = None, item_id: UUID | None = None,
         action: str | None = Query(None, pattern='^(create|review|practice|reschedule|switch|pause|resume|archive)$'),
         offset: int = Query(0, ge=0, le=100000), limit: int = Query(50, ge=1, le=50)):
    return service.history(db, user.id, plan_id=plan_id, item_id=item_id, action=action, offset=offset, limit=limit)
