from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

from app.main import _authorized, _plan_prompt, require_api_key
from app.models import ChatMessage
from app.settings import BASE_DIR, settings


class ApiAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    def _request(self, authorization: str | None = None, path: str = "/v1/models") -> Request:
        headers = []
        if authorization is not None:
            headers.append((b"authorization", authorization.encode("latin-1")))
        return Request({
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 9920),
        })

    def test_bearer_token_comparison_is_case_insensitive_for_scheme(self) -> None:
        with patch.object(settings, "api_key", "test-secret"):
            self.assertTrue(_authorized("bearer test-secret"))
            self.assertTrue(_authorized("Bearer test-secret"))
            self.assertFalse(_authorized("Bearer wrong"))
            self.assertFalse(_authorized("Basic test-secret"))
            self.assertFalse(_authorized(None))

    async def test_middleware_rejects_requests_when_key_is_not_configured(self) -> None:
        with patch.object(settings, "api_key", ""):
            response = await require_api_key(self._request(), AsyncMock())
        self.assertEqual(response.status_code, 503)

    async def test_middleware_rejects_missing_or_invalid_token(self) -> None:
        with patch.object(settings, "api_key", "test-secret"):
            response = await require_api_key(self._request(), AsyncMock())
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers.get("www-authenticate"), "Bearer")

    async def test_middleware_allows_valid_token_and_preflight(self) -> None:
        call_next = AsyncMock(return_value="passed")
        with patch.object(settings, "api_key", "test-secret"):
            response = await require_api_key(self._request("Bearer test-secret"), call_next)
            preflight = await require_api_key(
                Request({
                    "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                    "method": "OPTIONS", "scheme": "http", "path": "/v1/models",
                    "raw_path": b"/v1/models", "query_string": b"", "headers": [],
                    "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 9920),
                }),
                call_next,
            )
        self.assertEqual(response, "passed")
        self.assertEqual(preflight, "passed")
        self.assertEqual(call_next.await_count, 2)


class ConversationIsolationTests(unittest.TestCase):
    def test_each_request_rebuilds_full_transcript_and_starts_fresh_chat(self) -> None:
        messages = [
            ChatMessage(role="user", content="first turn"),
            ChatMessage(role="assistant", content="first answer"),
            ChatMessage(role="user", content="second turn"),
        ]
        prompt, new_chat, state = _plan_prompt("chatgpt", messages, [])
        self.assertTrue(new_chat)
        self.assertEqual(state, [])
        self.assertIn("first turn", prompt)
        self.assertIn("first answer", prompt)
        self.assertIn("second turn", prompt)

    def test_tool_requests_also_use_fresh_chat(self) -> None:
        messages = [ChatMessage(role="user", content="call the tool")]
        tools = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]
        prompt, new_chat, state = _plan_prompt("chatgpt", messages, tools)
        self.assertTrue(new_chat)
        self.assertEqual(state, [])
        self.assertIn("lookup", prompt)
        self.assertIn("call the tool", prompt)

    def test_vnc_services_bind_only_to_loopback(self) -> None:
        script = (BASE_DIR / "app" / "vnc_setup.sh").read_text(encoding="utf-8")
        self.assertIn("-nolisten tcp", script)
        self.assertIn("-localhost -listen 127.0.0.1", script)
        self.assertIn('"127.0.0.1:${NOVNC_PORT}" "127.0.0.1:${VNC_PORT}"', script)
        self.assertNotIn("-listen 0.0.0.0", script)
        self.assertNotIn('"0.0.0.0:${NOVNC_PORT}"', script)


if __name__ == "__main__":
    unittest.main()
