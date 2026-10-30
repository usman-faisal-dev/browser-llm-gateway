from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.url_security import validate_public_http_url


class ProviderStatus(str, Enum):
    disconnected = "disconnected"
    connected = "connected"
    error = "error"
    busy = "busy"


class ProviderMode(str, Enum):
    dom = "dom"
    network = "network"


class ProviderConfig(BaseModel):
    name: str = Field(min_length=1, pattern=r"^[a-zA-Z0-9_-]+$")
    display_name: str
    url: HttpUrl
    enabled: bool = True
    mode: ProviderMode = ProviderMode.dom
    input_selector: str
    send_button_selector: str | None = None
    response_selector: str
    response_text_selector: str | None = None
    stream_url_pattern: str | None = None
    user_data_dir: str | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: HttpUrl) -> HttpUrl:
        validate_public_http_url(str(value))
        return value


class ProviderRuntime(BaseModel):
    name: str
    display_name: str
    url: str
    enabled: bool
    mode: ProviderMode
    status: ProviderStatus
    error: str | None = None


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None = ""
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None


class ProviderCreateRequest(BaseModel):
    name: str = Field(min_length=1, pattern=r"^[a-zA-Z0-9_-]+$")
    display_name: str
    url: HttpUrl
    input_selector: str
    send_button_selector: str | None = None
    response_selector: str
    response_text_selector: str | None = None
    stream_url_pattern: str | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: HttpUrl) -> HttpUrl:
        validate_public_http_url(str(value))
        return value
