from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, Field


DocumentState = Literal["queued", "converting", "focusing", "readable", "ready", "error"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DocumentRecord(BaseModel):
    id: str
    file_name: str
    display_title: str | None = None
    author: str | None = None
    publication_year: int | None = None
    state: DocumentState = "queued"
    stage: str = "Queued"
    message: str = "Waiting to start"
    progress: float = Field(default=0, ge=0, le=1)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    page_count: int | None = None
    editable_word_count: int | None = None
    focus_word_count: int | None = None
    focus_provider: str | None = None
    focus_model: str | None = None
    focus_strategy_version: int | None = None
    content_sha256: str | None = None
    content_bytes: int | None = None
    error: str | None = None


class UploadResponse(BaseModel):
    document: DocumentRecord
    status_url: str
    cached: bool = False


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    focus_provider: str
    openrouter_configured: bool
    supported_extensions: list[str] = Field(default_factory=list)


class PagePreferences(BaseModel):
    dimmed_pages: list[int] = Field(default_factory=list)
    last_page: int = Field(default=1, ge=1)
    reading_mode: Literal["focus", "original"] = "focus"
    word_overrides: dict[str, dict[str, list[int]]] = Field(default_factory=dict)
    learned_terms: dict[str, int] = Field(default_factory=dict)


class PageDimRequest(BaseModel):
    dimmed: bool


class ReadingStateRequest(BaseModel):
    page: int = Field(ge=1)
    mode: Literal["focus", "original"]


class WordFocusRequest(BaseModel):
    focused: bool


class FocusAvailability(BaseModel):
    pages: list[int] = Field(default_factory=list)
    complete: bool = False


class ChapterLink(BaseModel):
    page: int
    label: str


class DocumentStructure(BaseModel):
    version: int = 1
    chapters: list[ChapterLink] = Field(default_factory=list)
    page_roles: dict[str, str] = Field(default_factory=dict)


class DeviceFocusRequest(BaseModel):
    words: list[Annotated[str, Field(min_length=1, max_length=512)]] = Field(min_length=1, max_length=4096)
