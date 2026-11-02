"""V5 additions only. The preview table is expiring authorization, not content."""
from copy import deepcopy

from app.modules.personalization.models import LearningAnalysis, UserHomePreferences
from app.modules.personalization.archive_contract import TABLE_FIELDS, validate_collections

FIELDS = {UserHomePreferences: TABLE_FIELDS['user_home_preferences'],
          LearningAnalysis: TABLE_FIELDS['learning_analyses']}


def prepare_restore(rows):
    """After raw validation: restore content, require fresh AI opt-in, never resume."""
    for row in rows.get('user_home_preferences', []):
        row['preferences'] = {**deepcopy(row['preferences']), 'ai_analysis_enabled': False}
    # Existing restore AiRun logic must terminalize queued/running, clear
    # auth_session_id/input_messages/lease. Analysis rows retain frozen history.
    return rows
