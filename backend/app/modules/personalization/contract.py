"""Pure credential-free aggregate and archive contract; safe for offline verifiers."""
from copy import deepcopy
from datetime import date
import hashlib
import json
import math
import re
from zoneinfo import ZoneInfo

SYSTEM = ('你是私人学习事实解释助手。只解释提供的本人汇总数字，区分事实、规则解释和样本不足。'
          '阅读和时长不等于掌握，不推断身份、能力排名或他人情况；没有记录不等于没有学习。'
          '只提出基于记录的可选建议，不编造来源或调用工具。')
FACT_FIELDS = {
    'activity': {'total_seconds', 'reading_seconds', 'review_seconds', 'practice_seconds', 'quiz_seconds'},
    'reviews': {'formal_reviews', 'practice_count', 'daily_distinct_review_object_sum'},
    'quiz': {'submitted', 'graded', 'needs_review', 'actual_points', 'max_points', 'manually_corrected'},
    'reading_progress': {'recorded_notes', 'active_notes_read', 'active_notes_total'},
}


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(wire(value).encode('utf-8')).hexdigest()


def validate_summary(value):
    def require(ok):
        if not ok:
            raise ValueError('invalid_analysis_summary')
    require(isinstance(value, dict) and set(value) == {'source', 'facts', 'sample', 'interpretation'})
    require(value['interpretation'] == 'descriptive_facts_not_mastery')
    source, facts, sample = value['source'], value['facts'], value['sample']
    require(isinstance(source, dict) and set(source) == {'kind', 'date_from', 'date_to', 'timezone', 'fingerprint'})
    require(source['kind'] == 'learning_summary')
    for key in ('date_from', 'date_to'):
        require(isinstance(source[key], str) and date.fromisoformat(source[key]).isoformat() == source[key])
    require(1900 <= date.fromisoformat(source['date_from']).year <= date.fromisoformat(source['date_to']).year < 9999)
    require(0 <= (date.fromisoformat(source['date_to']) - date.fromisoformat(source['date_from'])).days < 93)
    require(isinstance(source['timezone'], str) and len(source['timezone']) <= 64)
    ZoneInfo(source['timezone'])
    require(isinstance(facts, dict) and set(facts) == set(FACT_FIELDS))
    for group, keys in FACT_FIELDS.items():
        require(isinstance(facts[group], dict) and set(facts[group]) == keys)
        for number in facts[group].values():
            require(type(number) in ({int, float} if group == 'activity' else {int}) and
                    0 <= number <= 1e15 and math.isfinite(number))
    insufficient = (facts['activity']['total_seconds'] == 0 and facts['reviews']['formal_reviews'] == 0
                    and facts['reviews']['practice_count'] == 0 and facts['quiz']['submitted'] == 0)
    require(isinstance(sample, dict) and set(sample) == {'insufficient', 'reason'} and
            type(sample['insufficient']) is bool and sample['insufficient'] == insufficient and
            sample['reason'] == ('no_recorded_activity' if insufficient else None))
    require(isinstance(source['fingerprint'], str) and re.fullmatch(r'[0-9a-f]{64}', source['fingerprint']))
    base_source = {key: item for key, item in source.items() if key != 'fingerprint'}
    # T32 summary_service deliberately uses its pre-existing default separators.
    expected = hashlib.sha256(json.dumps({'source': base_source, 'facts': facts, 'sample': sample},
        ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    require(source['fingerprint'] == expected)
    return deepcopy(value)


def messages(summary):
    summary = validate_summary(summary)
    return [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': wire(summary)}]


def preview_payload(summary, provider, model, provider_snapshot):
    return {'summary': validate_summary(summary), 'provider': provider, 'model': model,
            'provider_snapshot': deepcopy(provider_snapshot), 'input_messages': messages(summary)}


def rules(summary):
    validate_summary(summary)
    return [{'kind': 'rule_explanation', 'rule': 'recorded_activity_only',
             'text': '仅汇总本人已记录事件；没有记录不代表没有学习。'},
            {'kind': 'rule_explanation', 'rule': 'reading_not_mastery',
             'text': '阅读覆盖和学习时长不是知识掌握度；复习和测验分数仅反映这些记录。'},
            {'kind': 'sample', 'insufficient': summary['sample']['insufficient'],
             'text': '本范围缺少已记录活动，不能推断学习能力。' if summary['sample']['insufficient'] else
                     '存在活动记录，汇总结果不构成能力或掌握程度评估。'}]
