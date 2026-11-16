from typing import Literal

from pydantic import EmailStr, Field, field_validator

from app.schemas.common import CamelModel, UtcDatetime

Plan = Literal["free", "pro"]


class UserOut(CamelModel):
    id: str
    name: str
    email: str
    initials: str
    role: str
    workspace: str
    member_since: UtcDatetime
    google_connected: bool
    queries_today: int
    plan: Plan
    is_demo: bool


class UpdateMeRequest(CamelModel):
    name: str | None = None
    email: EmailStr | None = None

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("Name can't be empty.")
        if len(value) > 80:
            raise ValueError("Name must be 80 characters or fewer.")
        return value


class UsageCounter(CamelModel):
    used: int
    limit: int | None


class QueriesCounter(UsageCounter):
    resets_at: UtcDatetime


class UsageOut(CamelModel):
    plan: Plan
    documents: UsageCounter
    queries_today: QueriesCounter
    passages_indexed: int


class SettingsOut(CamelModel):
    strict_citations: bool
    stream_answers: bool
    weekly_digest: bool
    allow_sharing: bool


class SettingsPatch(CamelModel):
    strict_citations: bool | None = None
    stream_answers: bool | None = None
    weekly_digest: bool | None = None
    allow_sharing: bool | None = None


class PlanRequest(CamelModel):
    plan: Plan = Field(description="Target plan. No payment is taken.")


class GoogleConnectRequest(CamelModel):
    id_token: str = Field(min_length=1)
