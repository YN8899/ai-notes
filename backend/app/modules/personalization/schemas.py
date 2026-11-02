from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

Tile = Literal['notes', 'reviews', 'social', 'messages', 'shares']
Section = Literal['home', 'notes', 'cards', 'quizzes', 'reviews', 'learning', 'preferences',
                  'social', 'messages', 'community', 'profile', 'chat']
DEFAULT_TILES = ['notes', 'reviews', 'social', 'messages', 'shares']


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    tile_order: list[Tile] = Field(default_factory=lambda: DEFAULT_TILES.copy(), min_length=5, max_length=5)
    hidden_tiles: list[Tile] = Field(default_factory=list, max_length=5)
    default_section: Section = 'home'
    recommendations_enabled: StrictBool = False
    learning_signals_enabled: StrictBool = False
    subjects: list[StrictStr] = Field(default_factory=list, max_length=20)
    tags: list[StrictStr] = Field(default_factory=list, max_length=20)
    ai_analysis_enabled: StrictBool = False

    @field_validator('tile_order', 'hidden_tiles', 'subjects', 'tags')
    @classmethod
    def unique(cls, values):
        if len(values) != len(set(values)):
            raise ValueError('duplicate_preference')
        return values

    @field_validator('subjects', 'tags')
    @classmethod
    def terms(cls, values, info):
        maximum = 80 if info.field_name == 'subjects' else 64
        if any(not value or value != value.strip() or len(value) > maximum or
               any(ord(c) < 32 for c in value) for value in values):
            raise ValueError('invalid_interest')
        return values


class PreferencesIn(Preferences):
    revision: StrictInt = Field(ge=0, le=2147483646)


class RevisionIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: StrictInt = Field(ge=0, le=2147483646)


class PreferencesOut(Preferences):
    revision: int
    updated_at: datetime | None


class AnalysisPreviewIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    date_from: date
    date_to: date
    provider: StrictStr = Field(min_length=1, max_length=32)
    model: StrictStr = Field(min_length=1, max_length=256)


class AnalysisCreateIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preview_id: UUID
    preview_hash: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    request_key: UUID


class AnalysisPreviewOut(BaseModel):
    id: UUID
    preview_hash: str
    summary: dict
    provider: str
    model: str
    input_messages: list[dict]
    input_bytes: int
    expires_at: datetime
    planned_calls: int = 1
    tools: list[str] = Field(default_factory=list)


class AnalysisOut(BaseModel):
    id: UUID
    run_id: UUID
    summary: dict
    preview_hash: str
    provider: str
    model: str
    status: str
    answer: str
    usage: dict | None
    error_code: str | None
    created_at: datetime
    finished_at: datetime | None
