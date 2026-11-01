from fastapi import APIRouter, Query
from app.modules.learning import reminder_schemas as s, reminder_service as service
from app.shared.dependencies import CurrentUser, DB

router = APIRouter(prefix="/learning", tags=["learning-reminders"])


@router.get("/settings", response_model=s.LearningSettingsOut)
def settings(db: DB, user: CurrentUser):
    return service.get_settings(db, user.id)


@router.patch("/settings", response_model=s.LearningSettingsOut)
def update(data: s.LearningSettingsPatch, db: DB, user: CurrentUser):
    return service.patch_settings(db, user.id, data)


@router.get("/reminder-history", response_model=s.DailyDigestList)
def history(db: DB, user: CurrentUser, offset: int = Query(0, ge=0, le=100000),
            limit: int = Query(50, ge=1, le=50)):
    return service.history(db, user.id, offset, limit)
