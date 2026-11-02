"""Reusable owner-only descriptive learning aggregates for tools and learning analysis."""
import hashlib
import json

from app.modules.learning import stats_service


def aggregate(db, owner, date_from, date_to):
    """Reuse the existing bounded statistics authority; never return raw records."""
    stats = stats_service.statistics(db, owner, date_from, date_to)
    activity_names = ('total_seconds','reading_seconds','review_seconds','practice_seconds','quiz_seconds')
    review_names = ('formal_reviews','practice_count')
    quiz_names = ('submitted','graded','needs_review','actual_points','max_points','manually_corrected')
    facts = {
        'activity': {key: sum(getattr(day, key) for day in stats.days) for key in activity_names},
        'reviews': {**{key: sum(getattr(day,key) for day in stats.days) for key in review_names},
                    'daily_distinct_review_object_sum': sum(day.distinct_review_objects for day in stats.days)},
        'quiz': {key: sum(getattr(day.quiz,key) for day in stats.days) for key in quiz_names},
        'reading_progress': stats.reading_progress.model_dump(),
    }
    insufficient = facts['activity']['total_seconds'] == 0 and facts['reviews']['formal_reviews'] == 0 and facts['reviews']['practice_count'] == 0 and facts['quiz']['submitted'] == 0
    sample = {'insufficient': insufficient, 'reason': 'no_recorded_activity' if insufficient else None}
    source = {'kind': 'learning_summary', 'date_from': stats.date_from.isoformat(), 'date_to': stats.date_to.isoformat(), 'timezone': stats.timezone}
    source['fingerprint'] = hashlib.sha256(json.dumps({'source': source, 'facts': facts, 'sample': sample},
        ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return {'source': source, 'facts': facts, 'sample': sample, 'interpretation': 'descriptive_facts_not_mastery'}
