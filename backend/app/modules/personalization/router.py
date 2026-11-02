from datetime import date
from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.modules.personalization import analysis_service, preferences_service, recommendations, schemas as s
from app.shared.dependencies import CurrentUser, DB

router = APIRouter(prefix='/personalization', tags=['personalization'])


@router.get('/preferences', response_model=s.PreferencesOut)
def preferences(db: DB, user: CurrentUser):
    return preferences_service.output(db, user.id)


@router.put('/preferences', response_model=s.PreferencesOut)
def save_preferences(data: s.PreferencesIn, db: DB, user: CurrentUser):
    return preferences_service.save(db, user.id, data)


@router.post('/preferences/reset', response_model=s.PreferencesOut)
def reset_preferences(data: s.RevisionIn, db: DB, user: CurrentUser):
    return preferences_service.save(db, user.id, data, reset=True)


@router.get('/recommendations')
def recommendation_feed(db: DB, user: CurrentUser, cursor: str | None = None,
                        limit: int = Query(default=20, ge=1, le=50)):
    return recommendations.feed(db, user, cursor=cursor, limit=limit)


@router.get('/analysis/facts')
def analysis_facts(db: DB, user: CurrentUser, date_from: date | None = None, date_to: date | None = None):
    return analysis_service.facts(db, user.id, date_from, date_to)


@router.post('/analysis/preview', response_model=s.AnalysisPreviewOut)
def analysis_preview(data: s.AnalysisPreviewIn, request: Request, db: DB, user: CurrentUser):
    return analysis_service.preview(db, user.id, request.state.auth_session.id, data)


@router.post('/analysis', response_model=s.AnalysisOut, status_code=201)
def create_analysis(data: s.AnalysisCreateIn, request: Request, db: DB, user: CurrentUser):
    return analysis_service.create(db, user.id, request.state.auth_session.id, data)


@router.get('/analysis', response_model=list[s.AnalysisOut])
def analysis_history(db: DB, user: CurrentUser, limit: int = Query(default=20, ge=1, le=50)):
    return analysis_service.history(db, user.id, limit)


@router.get('/analysis/{identity}', response_model=s.AnalysisOut)
def analysis_detail(identity: UUID, db: DB, user: CurrentUser):
    return analysis_service.output(db, user.id, identity)


@router.post('/analysis/{identity}/cancel', response_model=s.AnalysisOut)
def cancel_analysis(identity: UUID, db: DB, user: CurrentUser):
    return analysis_service.cancel(db, user.id, identity)
