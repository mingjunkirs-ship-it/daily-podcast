from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SourceBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source_type: str = Field(pattern="^(rss|arxiv|newsapi)$")
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class SourceCreate(SourceBase):
    pass


class SourceUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    config: dict[str, Any] | None = None


class SourceRead(SourceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    last_sync_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class SettingsRead(BaseModel):
    values: dict[str, Any]


class SettingsUpdate(BaseModel):
    values: dict[str, Any]


class EpisodeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    trigger_type: str
    title: str
    overview: str
    script_text: str
    payload_json: str
    error_message: str
    item_count: int
    audio_file: str
    notes_file: str
    created_at: datetime
    completed_at: datetime | None


class RunNowResponse(BaseModel):
    message: str
    episode_id: int | None = None


class SourcePresetRead(BaseModel):
    preset_id: str
    name: str
    source_type: str
    enabled: bool
    description: str
    config: dict[str, Any]


class ImportPresetsRequest(BaseModel):
    preset_ids: list[str] | None = None
    overwrite_existing: bool = False


class ImportPresetsResponse(BaseModel):
    selected: int
    created: int
    updated: int
    skipped: int


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class AuthMeResponse(BaseModel):
    authenticated: bool
    username: str
    is_admin: bool = False


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(min_length=8, max_length=128)


class RegisterOptionsResponse(BaseModel):
    allow_register: bool
    require_admin_approval: bool


class UserRead(BaseModel):
    id: int
    username: str
    created_at: datetime
    is_admin: bool
    disabled: bool = False


class UserResetPasswordRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    new_password: str = Field(min_length=8, max_length=128)


class UserSetDisabledRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    disabled: bool


class PendingRegistrationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    created_at: datetime


class ConnectionTestResponse(BaseModel):
    ok: bool
    message: str


class AddRssSourceRequest(BaseModel):
    url: str = Field(min_length=8, max_length=1000)
    name: str | None = Field(default=None, max_length=120)
    enabled: bool = True


class BatchRssSourceItem(BaseModel):
    url: str = Field(min_length=8, max_length=1000)
    name: str | None = Field(default=None, max_length=120)
    enabled: bool = True
    keywords: str | list[str] | None = None
    max_items: int | None = Field(default=None, ge=1, le=200)


class ImportRssBatchRequest(BaseModel):
    items: list[BatchRssSourceItem] = Field(default_factory=list)
    overwrite_existing: bool = False


class ImportRssBatchResponse(BaseModel):
    received: int
    created: int
    updated: int
    skipped: int
    errors: list[str] = Field(default_factory=list)


class AddRssHubSourceRequest(BaseModel):
    route: str = Field(min_length=2, max_length=1000)
    name: str | None = Field(default=None, max_length=120)
    enabled: bool = True


class RSSHubTemplateRead(BaseModel):
    key: str
    title: str
    route: str
    note: str


class SourceConnectivityTestResponse(BaseModel):
    source_id: int
    source_name: str
    ok: bool
    item_count: int
    message: str


class PromptVersionRead(BaseModel):
    id: str
    name: str
    created_at: datetime
    prompts: dict[str, str]


class PromptVersionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class CronTestRequest(BaseModel):
    schedule_cron: str = Field(min_length=5, max_length=120)
    timezone: str | None = Field(default=None, max_length=80)


class CronTestResponse(BaseModel):
    ok: bool
    message: str
    next_runs: list[str] = Field(default_factory=list)


class CronNaturalRequest(BaseModel):
    text: str = Field(min_length=2, max_length=120)


class CronNaturalResponse(BaseModel):
    ok: bool
    cron: str
    message: str


class EdgeVoicePreviewRequest(BaseModel):
    voice: str = Field(min_length=3, max_length=120)
    audio_speed: float | None = Field(default=None, ge=0.25, le=4.0)
