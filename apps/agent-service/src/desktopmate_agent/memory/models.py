from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class MemoryScope(StrEnum):
    SESSION = "session"
    USER = "user"
    PROJECT = "project"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    CANDIDATE = "candidate"
    REJECTED = "rejected"
    DELETED = "deleted"


class MemorySensitivity(StrEnum):
    NORMAL = "normal"
    PRIVATE = "private"
    SECRET = "secret"


class MemoryEntry(BaseModel):
    id: str | None = None
    scope: MemoryScope = MemoryScope.SESSION
    subject_id: str | None = None
    session_id: str | None = None
    key: str = Field(min_length=1)
    value: str = Field(min_length=1)
    source: str = Field(default="manual", min_length=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    sensitivity: MemorySensitivity = MemorySensitivity.NORMAL
    status: MemoryStatus = MemoryStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def default_subject_id(self) -> "MemoryEntry":
        if self.subject_id is None:
            self.subject_id = self.session_id
        if self.subject_id is None:
            raise ValueError("subject_id is required when session_id is not provided")
        return self
