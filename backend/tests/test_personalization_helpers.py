"""Pure tests: can run standalone without project conftest, settings, DB or network."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).parents[1] / 'app/modules/personalization'


def load(name):
    spec = importlib.util.spec_from_file_location('_t33_test_' + name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


c, s, a = load('contract'), load('schemas'), load('archive_contract')


def summary():
    source = {'kind': 'learning_summary', 'date_from': '2026-11-01', 'date_to': '2026-11-01', 'timezone': 'Asia/Shanghai'}
    facts = {group: {name: 0 for name in names} for group, names in c.FACT_FIELDS.items()}
    sample = {'insufficient': True, 'reason': 'no_recorded_activity'}
    source['fingerprint'] = hashlib.sha256(json.dumps({'source': source, 'facts': facts, 'sample': sample},
        ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return {'source': source, 'facts': facts, 'sample': sample, 'interpretation': 'descriptive_facts_not_mastery'}


def archive():
    owner, identity, run_id, conversation_id = (str(uuid4()) for _ in range(4))
    profile = {'fingerprint': 'a' * 64, 'capabilities': {'text': True, 'vision': False, 'tools': False, 'streaming': True}}
    payload = c.preview_payload(summary(), 'synthetic', 'local-test', profile)
    row = {'id': identity, 'created_at': '2026-11-01T00:00:00Z', 'owner_id': owner, 'run_id': run_id,
           'request_key': str(uuid4()), 'request_hash': 'b'*64, 'preview_hash': c.digest(payload), 'snapshot': payload}
    run = {'id': run_id, 'owner_id': owner, 'conversation_id': conversation_id, 'provider': 'synthetic',
           'model': 'local-test', 'provider_snapshot': profile, 'source': None, 'tools': [], 'tool_scope': None,
           'prompt': '解释本次明确确认的本人学习汇总'}
    return owner, {'user_home_preferences': [{'owner_id': owner, 'revision': 2,
        'preferences': s.Preferences(ai_analysis_enabled=True).model_dump(), 'updated_at': '2026-11-01T00:00:00Z'}],
        'learning_analyses': [row], 'ai_runs': [run]}


def test_defaults_are_private_and_independent():
    first, second = s.Preferences(), s.Preferences()
    assert not first.recommendations_enabled and not first.learning_signals_enabled and not first.ai_analysis_enabled
    first.tile_order.reverse()
    assert second.tile_order == s.DEFAULT_TILES


@pytest.mark.parametrize('changes', [
    {'tile_order': ['notes']*5}, {'tile_order': ['notes','reviews','social','messages','audit']},
    {'hidden_tiles': ['notes','notes']}, {'default_section': 'users'}, {'default_section': 'jobs'},
    {'ai_analysis_enabled': 'true'}, {'recommendations_enabled': 1}, {'subjects': [' a']},
    {'subjects': ['a','a']}, {'tags': ['a\x00']}, {'tags': [5]}, {'owner_id': str(uuid4())},
])
def test_strict_preferences(changes):
    with pytest.raises(ValidationError):
        s.Preferences(**changes)


def test_strict_revision_and_exact_allowlist():
    with pytest.raises(ValidationError):
        s.PreferencesIn(revision=True)
    with pytest.raises(ValidationError):
        s.PreferencesIn(revision='2')


def test_aggregate_is_exact_and_fingerprint_matches_t32():
    assert c.validate_summary(summary()) == summary()
    assert c.messages(summary())[1]['content'] == c.wire(summary())
    assert c.rules(summary())[-1]['insufficient'] is True


@pytest.mark.parametrize('mutate', [
    lambda v: v['source'].update(title='private'),
    lambda v: v['facts']['activity'].update(total_seconds=float('nan')),
    lambda v: v['facts']['activity'].update(total_seconds=True),
    lambda v: v['facts']['activity'].update(total_seconds=10**1000),
    lambda v: v['facts']['quiz'].update(submitted=-1),
    lambda v: v['facts'].update(raw_notes=[]),
    lambda v: v['sample'].update(insufficient=False),
    lambda v: v.update(interpretation='mastered'),
    lambda v: v['source'].update(fingerprint='0'*64),
])
def test_summary_rejects_raw_data_and_changed_numbers(mutate):
    value = summary()
    mutate(value)
    with pytest.raises((ValueError, TypeError)):
        c.validate_summary(value)


def test_v5_roundtrip_contract_and_v1_v4_absence():
    owner, data = archive()
    a.validate_collections(data, owner)
    for version in range(1, 5):
        a.validate_collections({'ai_runs': []}, owner, version)
        with pytest.raises(ValueError):
            a.validate_collections(data, owner, version)


@pytest.mark.parametrize('mutate', [
    lambda v: v['learning_analyses'][0].update(owner_id=str(uuid4())),
    lambda v: v['learning_analyses'][0].update(run_id=str(uuid4())),
    lambda v: v['learning_analyses'][0]['snapshot']['input_messages'][1].update(content='private raw notes'),
    lambda v: v['learning_analyses'].append(deepcopy(v['learning_analyses'][0])),
    lambda v: v['user_home_preferences'].append(deepcopy(v['user_home_preferences'][0])),
    lambda v: v['user_home_preferences'][0]['preferences'].update(default_section='audit'),
    lambda v: v['ai_runs'][0].update(tools=['search_notes']),
    lambda v: v['learning_analyses'][0]['snapshot'].update(api_key='forbidden'),
    lambda v: v['learning_analyses'][0]['snapshot']['summary']['facts']['activity'].update(total_seconds=10**1000),
])
def test_archive_owner_scope_duplicates_and_raw_payload_are_rejected(mutate):
    owner, data = archive()
    mutate(data)
    with pytest.raises((ValueError, ValidationError)):
        a.validate_collections(data, owner)
