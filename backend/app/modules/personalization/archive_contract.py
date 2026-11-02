"""No settings, DB or network imports: usable by the standalone ZIP verifier."""
from datetime import datetime
import importlib.util
from pathlib import Path
import re
from uuid import UUID

TABLE_FIELDS = {
    'user_home_preferences': ('owner_id', 'revision', 'preferences', 'updated_at'),
    'learning_analyses': ('id', 'created_at', 'owner_id', 'run_id', 'request_key', 'request_hash', 'preview_hash', 'snapshot'),
}


def require(ok):
    if not ok:
        raise ValueError('invalid_personalization_archive')


def sibling(name):
    spec = importlib.util.spec_from_file_location('_personalization_' + name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_preferences(value):
    # Keep the standalone verifier stdlib-only; tests lock this to schemas.py.
    keys = {'tile_order', 'hidden_tiles', 'default_section', 'recommendations_enabled',
            'learning_signals_enabled', 'subjects', 'tags', 'ai_analysis_enabled'}
    require(isinstance(value, dict) and set(value) == keys)
    tiles = {'notes', 'reviews', 'social', 'messages', 'shares'}
    for name in ('tile_order', 'hidden_tiles'):
        items = value[name]
        require(isinstance(items, list) and len(items) <= 5 and all(isinstance(item, str) and item in tiles for item in items)
                and len(items) == len(set(items)))
    require(len(value['tile_order']) == 5)
    require(value['default_section'] in {'home', 'notes', 'cards', 'quizzes', 'reviews', 'learning', 'preferences',
                                       'social', 'messages', 'community', 'profile', 'chat'})
    for name in ('recommendations_enabled', 'learning_signals_enabled', 'ai_analysis_enabled'):
        require(type(value[name]) is bool)
    for name, maximum in (('subjects', 80), ('tags', 64)):
        items = value[name]
        require(isinstance(items, list) and len(items) <= 20 and all(isinstance(item, str) and
                1 <= len(item) <= maximum and item == item.strip() and not any(ord(char) < 32 for char in item)
                for item in items) and len(items) == len(set(items)))


def identity(value):
    require(isinstance(value, str) and str(UUID(value)) == value)


def timestamp(value):
    require(isinstance(value, str) and datetime.fromisoformat(value).utcoffset() is not None)


def hexhash(value):
    require(isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value))


def validate_collections(collections, owner, version=5):
    """Validate RAW portable strings before restore changes operational fields."""
    contract = sibling('contract')
    if version < 5:
        require(not any(table in collections for table in TABLE_FIELDS))
        return
    require(all(table in collections and isinstance(collections[table], list) for table in TABLE_FIELDS))
    preferences = collections['user_home_preferences']
    require(len(preferences) <= 1)
    for row in preferences:
        require(isinstance(row, dict) and set(row) == set(TABLE_FIELDS['user_home_preferences']))
        require(row['owner_id'] == owner)
        identity(row['owner_id'])
        require(type(row['revision']) is int and 1 <= row['revision'] <= 2147483647)
        timestamp(row['updated_at'])
        validate_preferences(row['preferences'])
    runs = {row['id']: row for row in collections.get('ai_runs', [])}
    conversation_counts = {}
    for run in runs.values():
        key = run['conversation_id']
        conversation_counts[key] = conversation_counts.get(key, 0) + 1
    seen_runs, seen_requests, seen_ids = set(), set(), set()
    for row in collections['learning_analyses']:
        require(isinstance(row, dict) and set(row) == set(TABLE_FIELDS['learning_analyses']))
        for key in ('id', 'owner_id', 'run_id', 'request_key'):
            identity(row[key])
        require(row['owner_id'] == owner)
        timestamp(row['created_at'])
        for key, seen in (('id', seen_ids), ('run_id', seen_runs), ('request_key', seen_requests)):
            require(row[key] not in seen)
            seen.add(row[key])
        hexhash(row['request_hash']); hexhash(row['preview_hash'])
        payload = row['snapshot']
        require(isinstance(payload, dict) and set(payload) == {'summary', 'provider', 'model', 'provider_snapshot', 'input_messages'})
        require(isinstance(payload['provider'], str) and re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', payload['provider']))
        require(isinstance(payload['model'], str) and 1 <= len(payload['model']) <= 256 and
                not any(ord(char) < 32 for char in payload['model']))
        profile = payload['provider_snapshot']
        require(isinstance(profile, dict) and set(profile) == {'fingerprint', 'capabilities'})
        hexhash(profile['fingerprint'])
        caps = profile['capabilities']
        require(isinstance(caps, dict) and set(caps) == {'text', 'vision', 'tools', 'streaming'} and
                all(type(item) is bool for item in caps.values()) and caps['text'] and caps['streaming'])
        require(payload == contract.preview_payload(payload['summary'], payload['provider'], payload['model'], profile))
        require(row['preview_hash'] == contract.digest(payload))
        require(sum(len(item['content'].encode()) for item in payload['input_messages']) <= 32000)
        run = runs.get(row['run_id'])
        require(run is not None and run['owner_id'] == owner and run['source'] is None and run['tools'] == [] and
                run['tool_scope'] is None and run['provider'] == payload['provider'] and run['model'] == payload['model'] and
                run['provider_snapshot'] == profile and run['prompt'] == '解释本次明确确认的本人学习汇总')
        # A reserved analysis conversation may not be used to add raw chat input.
        require(conversation_counts[run['conversation_id']] == 1)
