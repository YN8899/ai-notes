from datetime import date
from typing import Literal
from fastapi import APIRouter
from app.modules.learning import stats_schemas as s, stats_service as service
from app.shared.dependencies import CurrentUser, DB

router = APIRouter(prefix='/learning', tags=['learning-statistics'])


@router.post('/events', response_model=s.ActivityBatchOut)
def events(data: s.ActivityBatchIn, db: DB, user: CurrentUser):
    return service.record(db, user.id, data)


@router.get('/stats', response_model=s.StatsOut)
def statistics(db: DB, user: CurrentUser, date_from: date | None = None, date_to: date | None = None):
    return service.statistics(db, user.id, date_from, date_to)


@router.get('/calendar', response_model=s.CalendarOut)
def calendar(db: DB, user: CurrentUser, date_from: date | None = None, date_to: date | None = None,
             kind: Literal['note','card','quiz'] | None = None):
    return service.calendar(db, user.id, date_from, date_to, kind)
