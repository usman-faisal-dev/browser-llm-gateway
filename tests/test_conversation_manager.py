from __future__ import annotations

import asyncio
import unittest

from app.conversation_manager import ConversationBusyError, ConversationManager
from app.models import ChatMessage


class ConversationManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_request_starts_new_chat_and_commits_after_success(self) -> None:
        manager = ConversationManager()
        messages = [ChatMessage(role="user", content="first")]
        plan = await manager.begin("chatgpt", "task-1", messages)

        self.assertTrue(plan.new_chat)
        self.assertEqual(plan.start_index, 0)
        await manager.finish(plan)

        next_messages = [
            ChatMessage(role="user", content="first"),
            ChatMessage(role="assistant", content="answer one"),
            ChatMessage(role="user", content="second"),
        ]
        next_plan = await manager.begin("chatgpt", "task-1", next_messages)
        self.assertFalse(next_plan.new_chat)
        self.assertEqual(next_plan.start_index, 1)
        await manager.finish(next_plan)

    async def test_diverged_or_shortened_history_starts_new_chat(self) -> None:
        manager = ConversationManager()
        original = [
            ChatMessage(role="user", content="first"),
            ChatMessage(role="assistant", content="answer"),
        ]
        first = await manager.begin("chatgpt", "task-1", original)
        await manager.finish(first)

        diverged = [
            ChatMessage(role="user", content="first"),
            ChatMessage(role="assistant", content="changed answer"),
            ChatMessage(role="user", content="next"),
        ]
        plan = await manager.begin("chatgpt", "task-1", diverged)
        self.assertTrue(plan.new_chat)
        self.assertEqual(plan.start_index, 0)
        await manager.finish(plan)

        shortened = [ChatMessage(role="user", content="first")]
        plan = await manager.begin("chatgpt", "task-1", shortened)
        self.assertTrue(plan.new_chat)
        await manager.finish(plan)

    async def test_failed_request_forces_rebuild(self) -> None:
        manager = ConversationManager()
        plan = await manager.begin(
            "chatgpt", "task-1", [ChatMessage(role="user", content="first")]
        )
        await manager.fail(plan)

        retry = await manager.begin(
            "chatgpt",
            "task-1",
            [
                ChatMessage(role="user", content="first"),
                ChatMessage(role="assistant", content="answer"),
                ChatMessage(role="user", content="second"),
            ],
        )
        self.assertTrue(retry.new_chat)
        await manager.finish(retry)

    async def test_overlapping_requests_for_same_conversation_are_rejected(self) -> None:
        manager = ConversationManager()
        plan = await manager.begin(
            "chatgpt", "task-1", [ChatMessage(role="user", content="first")]
        )
        with self.assertRaises(ConversationBusyError):
            await manager.begin(
                "chatgpt", "task-1", [ChatMessage(role="user", content="first")]
            )
        await manager.finish(plan)

    async def test_conversation_ids_and_providers_are_isolated(self) -> None:
        manager = ConversationManager()
        first = await manager.begin(
            "chatgpt", "same-id", [ChatMessage(role="user", content="first")]
        )
        await manager.finish(first)

        other_id = await manager.begin(
            "chatgpt", "other-id", [ChatMessage(role="user", content="first")]
        )
        other_provider = await manager.begin(
            "gemini", "same-id", [ChatMessage(role="user", content="first")]
        )
        self.assertTrue(other_id.new_chat)
        self.assertTrue(other_provider.new_chat)
        await manager.finish(other_id)
        await manager.finish(other_provider)

    async def test_assistant_only_suffix_rebuilds_instead_of_sending_empty_prompt(self) -> None:
        manager = ConversationManager()
        first = await manager.begin(
            "chatgpt", "task-1", [ChatMessage(role="user", content="first")]
        )
        await manager.finish(first)

        repeated = await manager.begin(
            "chatgpt",
            "task-1",
            [
                ChatMessage(role="user", content="first"),
                ChatMessage(role="assistant", content="answer"),
            ],
        )
        # The newly appended assistant response is already represented by the provider.
        # A full rebuild is safer than issuing an empty provider turn.
        self.assertTrue(repeated.new_chat)
        await manager.finish(repeated)


if __name__ == "__main__":
    unittest.main()
