"""Pure standard-library validation of portable T32 history; no settings, DB or network."""
import json
import re
from datetime import date
from uuid import UUID
from zoneinfo import ZoneInfo

TOOLS = {'search_notes', 'read_note_excerpt', 'learning_summary'}
RUN_FIELDS = ('provider', 'provider_snapshot', 'tools', 'tool_scope')
LEGACY_RUN_FIELDS = frozenset('id created_at owner_id conversation_id model prompt answer status usage error_code finished_at source progress'.split())
TOOL_FIELDS = frozenset('id created_at owner_id run_id step_id position provider_call_id tool_name arguments result sources status error_code finished_at'.split())


def require(condition):
    if not condition:
        raise ValueError('invalid_ai_history')


def identity(value):
    require(isinstance(value, str) and str(UUID(value)) == value)


def integer(value, low=0, high=10_000_000):
    require(type(value) is int and low <= value <= high)


def text(value, maximum, *, nonempty=True):
    require(isinstance(value, str) and len(value) <= maximum and '\x00' not in value and (not nonempty or bool(value.strip())))
    value.encode('utf-8')


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result)
        result[key] = value
    return result


def decode(raw):
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: require(False))


def learning_source(source):
    require(isinstance(source, dict) and set(source) == {'kind', 'date_from', 'date_to', 'timezone', 'fingerprint'})
    require(source['kind'] == 'learning_summary')
    for key in ('date_from', 'date_to'):
        require(isinstance(source[key], str) and str(date.fromisoformat(source[key])) == source[key])
    left, right = date.fromisoformat(source['date_from']), date.fromisoformat(source['date_to'])
    require(1900 <= left.year <= right.year < 9999 and 0 <= (right-left).days < 93)
    text(source['timezone'], 80)
    ZoneInfo(source['timezone'])
    require(isinstance(source['fingerprint'], str) and re.fullmatch('[a-f0-9]{64}', source['fingerprint']))


def note_source(source):
    require(isinstance(source, dict) and set(source) == {'note_id', 'revision', 'title', 'start', 'end'})
    identity(source['note_id']); integer(source['revision'], 1, 2**31-1); text(source['title'], 200)
    integer(source['start']); integer(source['end']);require(source['end'] >= source['start'])


def validate_run(record):
    require(isinstance(record['provider'], str) and re.fullmatch('[a-z][a-z0-9_-]{0,31}', record['provider']))
    text(record['model'], 256)
    require(not any(ord(c) < 32 for c in record['model']))
    names = record['tools']
    require(isinstance(names, list) and len(names) <= 3 and all(isinstance(n, str) and n in TOOLS for n in names) and len(set(names)) == len(names))
    snapshot = record['provider_snapshot']
    if snapshot is None:
        require(not names and record['provider'] == 'deepseek')
    else:
        require(isinstance(snapshot, dict) and set(snapshot) == {'fingerprint', 'capabilities'})
        require(isinstance(snapshot['fingerprint'], str) and re.fullmatch('[a-f0-9]{64}', snapshot['fingerprint']))
        caps = snapshot['capabilities']
        require(isinstance(caps, dict) and set(caps) == {'text', 'vision', 'tools', 'streaming'} and all(type(v) is bool for v in caps.values()))
        require(not names or caps['tools'])
    scope = record['tool_scope']
    if not names:
        require(scope is None)
        return
    require(isinstance(scope, dict) and {'version', 'notes'} <= set(scope) <= {'version', 'notes', 'learning_summary'})
    require(type(scope['version']) is int and scope['version'] == 1 and isinstance(scope['notes'], list) and len(scope['notes']) <= 100)
    require(bool(scope['notes']) == bool(set(names) & {'search_notes', 'read_note_excerpt'}))
    seen = set()
    for source in scope['notes']:
        require(isinstance(source, dict) and set(source) == {'note_id', 'revision', 'title'})
        note_source({**source, 'start': 0, 'end': 0})
        require(source['note_id'] not in seen);seen.add(source['note_id'])
    require(('learning_summary' in names) == ('learning_summary' in scope))
    if 'learning_summary' in scope:
        learning_source(scope['learning_summary'])


def arguments(name, values):
    require(isinstance(values, dict))
    if name == 'search_notes':
        require({'query'} <= set(values) <= {'query', 'limit'});text(values['query'], 120)
        require(not any(ord(c)<32 for c in values['query']));integer(values.get('limit', 5), 1, 5)
    elif name == 'read_note_excerpt':
        require({'note_id'} <= set(values) <= {'note_id', 'start', 'length'})
        identity(values['note_id']);integer(values.get('start', 0));integer(values.get('length', 1000), 1, 1500)
    else:
        require(name == 'learning_summary' and not values)


def validate_tool(record, run, step):
    require(str(record['owner_id']) == str(run['owner_id']) and str(record['run_id']) == str(run['id']))
    require(str(record['step_id']) == str(step['id']) and str(step['run_id']) == str(run['id']) and step['kind'] == 'chat')
    integer(record['position'], 0, 5)
    require(isinstance(record['provider_call_id'], str) and re.fullmatch('[A-Za-z0-9_-]{1,128}', record['provider_call_id']))
    name = record['tool_name'];require(name in run['tools'] and name in TOOLS);arguments(name, record['arguments'])
    sources, result, status = record['sources'], record['result'], record['status']
    require(isinstance(sources, list) and len(sources) <= 5 and status in {'pending', 'succeeded', 'failed', 'interrupted'})
    scope = {s['note_id']: s for s in run['tool_scope']['notes']}
    if name == 'read_note_excerpt' and record['arguments']['note_id'] not in scope:
        require(status != 'succeeded' and result is None)
    for source in sources:
        if name == 'learning_summary':
            learning_source(source);require(source == run['tool_scope']['learning_summary'])
        else:
            note_source(source);frozen = scope.get(source['note_id'])
            require(frozen and source['revision'] == frozen['revision'] and source['title'] == frozen['title'])
    require((status == 'succeeded') == (result is not None))
    require(status == 'succeeded' or not sources)
    require((status == 'pending') == (record['finished_at'] is None))
    if result is None:
        return
    text(result, 8192, nonempty=False);require(len(result.encode('utf-8')) <= 8192)
    payload = decode(result)
    if name == 'learning_summary':
        require(isinstance(payload, dict) and set(payload) == {'source', 'facts', 'sample', 'interpretation'})
        require(sources == [payload['source']] and payload['source'] == run['tool_scope']['learning_summary'])
        require(payload['interpretation'] == 'descriptive_facts_not_mastery')
        sample = payload['sample']
        require(isinstance(sample, dict) and set(sample) == {'insufficient', 'reason'} and type(sample['insufficient']) is bool)
        require(sample['reason'] == ('no_recorded_activity' if sample['insufficient'] else None))
        require(isinstance(payload['facts'], dict) and set(payload['facts']) == {'activity', 'reviews', 'quiz', 'reading_progress'})
        groups = {'activity': {'total_seconds','reading_seconds','review_seconds','practice_seconds','quiz_seconds'},
            'reviews': {'formal_reviews','practice_count','daily_distinct_review_object_sum'},
            'quiz': {'submitted','graded','needs_review','actual_points','max_points','manually_corrected'},
            'reading_progress': {'recorded_notes','active_notes_read','active_notes_total'}}
        for group, fields in groups.items():
            require(isinstance(payload['facts'][group], dict) and set(payload['facts'][group]) == fields)
            for value in payload['facts'][group].values():
                require((type(value) in {int,float} if group == 'activity' else type(value) is int) and value >= 0 and value < 1e15)
        return
    matches = payload.get('matches') if name == 'search_notes' else [payload]
    require(isinstance(matches, list) and len(matches) <= 5)
    if name == 'search_notes':
        require(set(payload) == {'matches', 'scope_note_count'} and type(payload['scope_note_count']) is int and payload['scope_note_count'] == len(scope))
    result_sources = []
    for match in matches:
        require(isinstance(match, dict) and set(match) == {'note_id','revision','title','start','end','excerpt'})
        text(match['excerpt'], 1500, nonempty=False)
        require(type(match['end']) is int and type(match['start']) is int and match['end']-match['start'] == len(match['excerpt']))
        result_sources.append({k:v for k,v in match.items() if k != 'excerpt'})
    require(result_sources == sources)
