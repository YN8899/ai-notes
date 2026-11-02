from sqlalchemy import select

from app.modules.personalization.models import UserHomePreferences
from app.modules.personalization.schemas import Preferences, PreferencesOut
from app.shared.db import utcnow
from app.shared.errors import AppError
from app.shared.models import User


def get(db, owner, *, lock=False):
    query = select(UserHomePreferences).where(UserHomePreferences.owner_id == owner)
    row = db.scalar(query.with_for_update() if lock else query)
    content = Preferences.model_validate(row.preferences) if row else Preferences()
    return row, content


def output(db, owner):
    row, content = get(db, owner)
    return PreferencesOut(**content.model_dump(), revision=row.revision if row else 0,
                          updated_at=row.updated_at if row else None)


def save(db, owner, data, *, reset=False):
    # Matches authenticated mutations and serializes first insert + final paid gates.
    db.scalar(select(User).where(User.id == owner).with_for_update())
    row, _ = get(db, owner, lock=True)
    if data.revision != (row.revision if row else 0):
        raise AppError(409, 'home_preferences_changed', '偏好已在其他设备修改；请保留当前输入并重新读取后合并')
    content = Preferences() if reset else Preferences.model_validate(data.model_dump(exclude={'revision'}))
    if row is None:
        row = UserHomePreferences(owner_id=owner, revision=1, preferences=content.model_dump())
        db.add(row)
    else:
        row.revision += 1
        row.preferences = content.model_dump()
        row.updated_at = utcnow()
    db.flush()
    # Turning off the feature cancels queued work; running attempts still use final gate.
    if not content.ai_analysis_enabled:
        from app.modules.personalization.analysis_service import cancel_active
        cancel_active(db, owner)
    db.commit()
    return output(db, owner)
