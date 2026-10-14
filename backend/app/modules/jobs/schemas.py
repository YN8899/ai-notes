from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class DiagnosticIn(BaseModel):
    request_key: str = Field(min_length=8, max_length=64)
    duration_seconds: int = Field(default=0, ge=0, le=30)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: str
    status: Literal["queued", "running", "succeeded", "failed"]
    attempts: int
    max_attempts: int
    result: dict | None
    error_code: str | None
    run_after: datetime
    lease_until: datetime | None
    created_at: datetime


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    message: str
    read_at: datetime | None
    created_at: datetime
