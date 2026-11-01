"""Pure IANA/time validators; no DB and no notification delivery required."""
import pytest
from pydantic import ValidationError
from app.modules.learning.reminder_schemas import LearningSettingsPatch


@pytest.mark.parametrize("zone", ["Asia/Shanghai", "UTC", "America/New_York", "Pacific/Kiritimati"])
def test_real_iana_zones_and_preferences(zone):
    value = LearningSettingsPatch(revision=1, timezone=zone, review_time="20:00", reminder_enabled=False)
    assert value.timezone == zone and value.reminder_enabled is False


@pytest.mark.parametrize("value", ["25:00", "20:60", "8:00", "20:00:00", "20:00\n", None])
def test_clock_requires_exact_hour_minute(value):
    with pytest.raises(ValidationError):
        LearningSettingsPatch(revision=1, review_time=value)


@pytest.mark.parametrize("zone", ["", "unknown/zone", "../etc/passwd", "/etc/passwd", None])
def test_invalid_zone_is_rejected(zone):
    with pytest.raises(ValidationError):
        LearningSettingsPatch(revision=1, timezone=zone)


@pytest.mark.parametrize("value", [None, 1, "false"])
def test_explicit_boolean_only(value):
    with pytest.raises(ValidationError):
        LearningSettingsPatch(revision=1, reminder_enabled=value)
