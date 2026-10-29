from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from app.models import ProviderConfig, ProviderCreateRequest
from app.settings import PROVIDERS_DIR, ensure_data_dirs


class ProviderStore:
    def __init__(self, providers_dir: Path = PROVIDERS_DIR) -> None:
        self.providers_dir = providers_dir
        ensure_data_dirs()

    def list(self) -> list[ProviderConfig]:
        providers: list[ProviderConfig] = []
        for path in sorted(self.providers_dir.glob("*.json")):
            try:
                providers.append(self.load(path.stem))
            except (OSError, json.JSONDecodeError, ValidationError):
                continue
        return providers

    def load(self, name: str) -> ProviderConfig:
        path = self._path_for(name)
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        return ProviderConfig.model_validate(payload)

    def upsert(self, request: ProviderCreateRequest) -> ProviderConfig:
        provider = ProviderConfig(
            name=request.name,
            display_name=request.display_name,
            url=request.url,
            input_selector=request.input_selector,
            send_button_selector=request.send_button_selector,
            response_selector=request.response_selector,
            response_text_selector=request.response_text_selector,
            stream_url_pattern=request.stream_url_pattern,
        )
        path = self._path_for(provider.name)
        with path.open("w", encoding="utf-8") as file:
            json.dump(provider.model_dump(mode="json", exclude_none=True), file, indent=2)
            file.write("\n")
        return provider

    def toggle(self, name: str) -> ProviderConfig:
        provider = self.load(name)
        provider.enabled = not provider.enabled
        self._save(provider)
        return provider

    def delete(self, name: str) -> None:
        self._path_for(name).unlink()

    def exists(self, name: str) -> bool:
        return self._path_for(name).exists()

    def _save(self, provider: ProviderConfig) -> None:
        path = self._path_for(provider.name)
        with path.open("w", encoding="utf-8") as file:
            json.dump(provider.model_dump(mode="json", exclude_none=True), file, indent=2)
            file.write("\n")

    def _path_for(self, name: str) -> Path:
        return self.providers_dir / f"{name}.json"
