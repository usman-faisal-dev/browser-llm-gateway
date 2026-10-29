from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.models import ProviderCreateRequest
from app.provider_store import ProviderStore


def provider_request(name: str = "test") -> ProviderCreateRequest:
    return ProviderCreateRequest(
        name=name,
        display_name="Test",
        url="https://example.com/",
        input_selector="textarea",
        response_selector=".response",
    )


class ProviderStoreTests(unittest.TestCase):
    def test_toggle_flips_enabled_and_saves(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProviderStore(Path(temp_dir))
            store.upsert(provider_request())

            disabled = store.toggle("test")
            reloaded = store.load("test")

            self.assertFalse(disabled.enabled)
            self.assertFalse(reloaded.enabled)

            enabled = store.toggle("test")

            self.assertTrue(enabled.enabled)
            self.assertTrue(store.load("test").enabled)

    def test_delete_removes_provider_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProviderStore(Path(temp_dir))
            store.upsert(provider_request())

            store.delete("test")

            self.assertFalse(store.exists("test"))


if __name__ == "__main__":
    unittest.main()
