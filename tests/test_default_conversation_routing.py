from __future__ import annotations

import unittest

from app.conversation_manager import ConversationBusyError, ConversationManager
from app.main import DEFAULT_PROVIDER_CONVERSATION_ID, _effective_conversation_id
from app.models import ChatMessage


class DefaultConversationRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_request_without_header_uses_provider_default_conversation(self) -> None:
        tools = [{"type": "function", "function": {"name": "lookup", "parameters": {}}}]
        self.assertEqual(
            _effective_conversation_id(None, tools),
            DEFAULT_PROVIDER_CONVERSATION_ID,
        )
        # The reserved internal key cannot pass public header validation.
        self.assertTrue(any(ord(char) < 33 for char in DEFAULT_PROVIDER_CONVERSATION_ID))

    async def test_explicit_id_is_preserved_even_without_tools(self) -> None:
        self.assertEqual(_effective_conversation_id("client-session-1", []), "client-session-1")

    async def test_no_id_non_tool_request_remains_stateless(self) -> None:
        self.assertIsNone(_effective_conversation_id(None, []))

    async def test_default_provider_conversation_persists_and_rejects_overlap(self) -> None:
        manager = ConversationManager()
        conversation_id = DEFAULT_PROVIDER_CONVERSATION_ID
        first_messages = [ChatMessage(role="user", content="remember DEFAULT-SESSION-731")]
        first = await manager.begin("chatgpt", conversation_id, first_messages)
        self.assertTrue(first.new_chat)
        await manager.finish(first)

        followup_messages = [
            *first_messages,
            ChatMessage(role="assistant", content="I will remember it."),
            ChatMessage(role="user", content="What token did I give you?"),
        ]
        followup = await manager.begin("chatgpt", conversation_id, followup_messages)
        self.assertFalse(followup.new_chat)
        self.assertEqual(followup.start_index, len(first_messages))
        await manager.fail(followup)

        active = await manager.begin("chatgpt", conversation_id, first_messages)
        with self.assertRaises(ConversationBusyError):
            await manager.begin("chatgpt", conversation_id, first_messages)
        await manager.finish(active)

    async def test_explicit_conversations_remain_isolated_from_default(self) -> None:
        manager = ConversationManager()
        default = await manager.begin(
            "chatgpt",
            DEFAULT_PROVIDER_CONVERSATION_ID,
            [ChatMessage(role="user", content="default conversation")],
        )
        explicit = await manager.begin(
            "chatgpt",
            "client-session-1",
            [ChatMessage(role="user", content="explicit conversation")],
        )
        self.assertTrue(default.new_chat)
        self.assertTrue(explicit.new_chat)
        await manager.finish(default)
        await manager.finish(explicit)


if __name__ == "__main__":
    unittest.main()
