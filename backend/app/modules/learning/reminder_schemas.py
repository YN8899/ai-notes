import re
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, Field, field_validator


class LearningSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(strict=True, ge=1)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    review_time: str | None = None
    reminder_enabled: bool | None = Field(default=None, strict=True)

    @field_validator("timezone")
    @classmethod
    def zone(cls, value):
        if value is None:
            raise ValueError("Timezone cannot be null")
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Unknown IANA timezone") from None
        return value

    @field_validator("review_time")
    @classmethod
    def clock(cls, value):
        if not isinstance(value, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
            raise ValueError("Use HH:MM")
        return value

    @field_validator("reminder_enabled")
    @classmethod
    def enabled(cls, value):
        if value is None:
            raise ValueError("Reminder preference cannot be null")
        return value


class LearningSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    timezone: str
    review_time: str
    reminder_enabled: bool
    revision: int
    updated_at: datetime


class DailyDigestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    local_date: date
    timezone: str
    counts: dict[str, int]
    created_at: datetime


class DailyDigestList(BaseModel):
    items: list[DailyDigestOut]
    total: int
    offset: int
    limit: int
