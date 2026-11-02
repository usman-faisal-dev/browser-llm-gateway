from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field

from app.models import ChatMessage


class ConversationBusyError(RuntimeError):
    """Raised when a client submits overlapping requests for one conversation."""


@dataclass
class ConversationState:
    provider_name: str
    conversation_id: str
    message_hashes: list[str] = field(default_factory=list)
    in_flight: bool = False
    needs_rebuild: bool = False
    last_activity: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class ConversationPlan:
    provider_name: str
    conversation_id: str
    message_hashes: tuple[str, ...]
    start_index: int
    new_chat: bool


class ConversationManager:
    """Tracks transcript synchronization for live provider conversations.

    State is in-memory in this first implementation. After a gateway restart,
    the next request rebuilds a provider conversation from the client's transcript.
    """

    def __init__(self) -> None:
        self._states: dict[tuple[str, str], ConversationState] = {}
        self._lock = asyncio.Lock()

    async def begin(
        self,
        provider_name: str,
        conversation_id: str,
        messages: list[ChatMessage],
    ) -> ConversationPlan:
        key = (provider_name, conversation_id)
        hashes = tuple(self._message_hash(message) for message in messages)
        async with self._lock:
            state = self._states.get(key)
            if state is not None and state.in_flight:
                raise ConversationBusyError(
                    "A request is already running for this conversation"
                )
            if state is None:
                state = ConversationState(provider_name, conversation_id)
                self._states[key] = state

            previous = state.message_hashes
            extends_history = (
                bool(previous)
                and len(hashes) > len(previous)
                and hashes[: len(previous)] == tuple(previous)
            )
            start_index = len(previous) if extends_history else 0
            new_chat = state.needs_rebuild or not extends_history

            # A suffix containing only assistant messages has no new input to send.
            if not new_chat and not any(
                message.role != "assistant" for message in messages[start_index:]
            ):
                new_chat = True
                start_index = 0

            state.in_flight = True
            state.last_activity = time.monotonic()
            return ConversationPlan(
                provider_name=provider_name,
                conversation_id=conversation_id,
                message_hashes=hashes,
                start_index=start_index,
                new_chat=new_chat,
            )

    async def finish(self, plan: ConversationPlan) -> None:
        key = (plan.provider_name, plan.conversation_id)
        async with self._lock:
            state = self._states.get(key)
            if state is None:
                return
            state.message_hashes = list(plan.message_hashes)
            state.in_flight = False
            state.needs_rebuild = False
            state.last_activity = time.monotonic()

    async def fail(self, plan: ConversationPlan) -> None:
        """Force a fresh provider chat next time; the prompt may have been received."""
        key = (plan.provider_name, plan.conversation_id)
        async with self._lock:
            state = self._states.get(key)
            if state is None:
                return
            state.in_flight = False
            state.needs_rebuild = True
            state.last_activity = time.monotonic()

    async def state_count(self) -> int:
        async with self._lock:
            return len(self._states)

    @staticmethod
    def _message_hash(message: ChatMessage) -> str:
        payload = {
            "role": message.role,
            "content": message.content,
            "tool_calls": message.tool_calls,
            "tool_call_id": message.tool_call_id,
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
