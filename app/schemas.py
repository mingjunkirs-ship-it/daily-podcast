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


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class ConnectionTestResponse(BaseModel):
    ok: bool
    message: str


class AddRssSourceRequest(BaseModel):
    url: str = Field(min_length=8, max_length=1000)
    name: str | None = Field(default=None, max_length=120)
    enabled: bool = True


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
