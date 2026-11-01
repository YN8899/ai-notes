"""Compare the adapter to the actual pinned official library, not a mock."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import pytest
from fsrs import Card, Rating, Scheduler
from app.modules.learning import scheduler as s
from app.shared.errors import AppError

NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("rating", [1, 2, 3, 4])
def test_four_ratings_match_official_library(rating):
    initial = s.initialize("fsrs", NOW, card_id=42)
    old = deepcopy(initial.scheduler_state)
    actual = s.schedule("fsrs", old, rating, NOW, algorithm_version=s.FSRS_VERSION)
    expected, _ = Scheduler(enable_fuzzing=False).review_card(Card(card_id=42, due=NOW), Rating(rating), review_datetime=NOW)
    assert actual.next_due_at == expected.due
    assert actual.scheduler_state == {"card": expected.to_dict()}
    assert old == initial.scheduler_state


def test_learning_review_lapse_sequence_serializes_and_resumes():
    official = Card(card_id=7, due=NOW)
    adapter = s.initialize("fsrs", NOW, card_id=7)
    when = NOW
    for rating in [3, 3, 4, 1, 2, 3, 4]:
        official, _ = Scheduler(enable_fuzzing=False).review_card(official, Rating(rating), review_datetime=when)
        adapter = s.schedule("fsrs", adapter.scheduler_state, rating, when)
        assert adapter.scheduler_state["card"] == official.to_dict()
        when = adapter.next_due_at


@pytest.mark.parametrize("rating", [0, 5, True, "3", None])
def test_invalid_rating_does_not_advance(rating):
    original = s.initialize("fsrs", NOW)
    with pytest.raises(AppError):
        s.schedule("fsrs", original.scheduler_state, rating, NOW)


@pytest.mark.parametrize("damage", ["keys", "nan", "negative", "future", "step", "bool"])
def test_corrupt_state_cannot_reach_official_algorithm(damage):
    state = s.initialize("fsrs", NOW).scheduler_state
    if damage == "keys": state["card"]["extra"] = "private-sentinel"
    elif damage == "nan": state["card"]["stability"] = float("nan")
    elif damage == "negative": state["card"]["difficulty"] = -1
    elif damage == "future": state["card"]["last_review"] = (NOW + timedelta(days=1)).isoformat()
    elif damage == "step": state["card"]["step"] = -1
    else: state["card"]["state"] = True
    with pytest.raises(AppError) as error:
        s.schedule("fsrs", state, 3, NOW)
    assert error.value.code == "scheduler_state_invalid"


def test_manual_schedule_requires_explicit_future_time_and_empty_current_state():
    stored = {}
    result = s.schedule("manual", stored, 3, NOW, manual_due=NOW+timedelta(days=2), algorithm_version=s.MANUAL_VERSION)
    assert result.next_due_at == NOW+timedelta(days=2) and result.scheduler_state == stored
    assert result.scheduler_state is not stored
    with pytest.raises(AppError): s.schedule("manual", stored, 3, NOW, algorithm_version=s.MANUAL_VERSION)
    with pytest.raises(AppError): s.schedule("manual", stored, 3, NOW, manual_due=NOW, algorithm_version=s.MANUAL_VERSION)


@pytest.mark.parametrize('state', [None, [], {'unknown': 1}, {'previous_fsrs': {}}])
def test_manual_rejects_unknown_state_instead_of_carrying_or_resetting_it(state):
    with pytest.raises(AppError) as error:
        s.schedule('manual', state, 3, NOW, algorithm_version=s.MANUAL_VERSION, manual_due=NOW+timedelta(days=2))
    assert error.value.code == 'scheduler_state_invalid'


@pytest.mark.parametrize('version', [None, 'manual/999'])
def test_manual_requires_current_known_version(version):
    with pytest.raises(AppError) as error:
        s.schedule('manual', {}, 3, NOW, algorithm_version=version, manual_due=NOW+timedelta(days=2))
    assert error.value.code == 'scheduler_state_invalid'


@pytest.mark.parametrize('state,step', [(1, 0), (2, None), (3, 0)])
@pytest.mark.parametrize('learned', [True, False])
def test_memory_parameters_and_last_review_must_exist_together(state, step, learned):
    value = s.initialize('fsrs', NOW).scheduler_state
    value['card'].update(state=state, step=step,
                         stability=1.0 if learned else None,
                         difficulty=5.0 if learned else None,
                         last_review=None if learned else (NOW-timedelta(days=1)).isoformat())
    with pytest.raises(AppError) as error:
        s.schedule('fsrs', value, 3, NOW, algorithm_version=s.FSRS_VERSION)
    assert error.value.code == 'scheduler_state_invalid'


def test_algorithm_upgrade_requires_explicit_strategy_migration():
    initial = s.initialize("fsrs", NOW)
    with pytest.raises(AppError):
        s.schedule("fsrs", initial.scheduler_state, 3, NOW, algorithm_version="future/99")


@pytest.mark.parametrize("state,step", [(2, None), (3, 0), (1, 0)])
def test_learned_states_require_complete_memory_parameters(state, step):
    value = s.initialize("fsrs", NOW).scheduler_state
    value["card"].update(state=state, step=step)
    if state == 1:
        value["card"]["difficulty"] = 5.0
    with pytest.raises(AppError) as error:
        s.schedule("fsrs", value, 3, NOW)
    assert error.value.code == "scheduler_state_invalid"


def test_timezones_normalize_to_utc_and_naive_times_rejected():
    other = NOW.astimezone(timezone(timedelta(hours=8)))
    assert s.initialize("fsrs", other).next_due_at == NOW
    with pytest.raises(AppError): s.initialize("fsrs", NOW.replace(tzinfo=None))
