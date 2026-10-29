from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.browser_engine import BrowserEngine
from app.models import ChatCompletionRequest, ChatMessage, ProviderCreateRequest
from app.provider_store import ProviderStore
from app.settings import (
    BASE_DIR,
    COMMON_TIMEZONES,
    BrowserFingerprintSettings,
    USER_AGENT_PLATFORM_MAP,
    USER_AGENT_PRESETS,
    load_browser_fingerprint_settings,
    save_browser_fingerprint_settings,
    settings,
)


store = ProviderStore()
engine = BrowserEngine(store)
DASHBOARD_PATH = BASE_DIR / "app" / "dashboard.html"
NOVNC_PATH = BASE_DIR / "novnc"
VNC_SETUP_PATH = BASE_DIR / "app" / "vnc_setup.sh"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    os.environ.setdefault("DISPLAY", ":99")
    if os.uname().sysname != "Darwin":
        _start_vnc()
    await engine.start()
    # Auto-restore saved sessions
    from app.settings import SESSIONS_DIR
    for p in store.list():
        if not p.enabled:
            continue
        session_file = SESSIONS_DIR / f"{p.name}.json"
        if session_file.exists():
            try:
                await engine.login(p.name)
            except Exception:
                pass  # session may be stale, user can re-open
    try:
        yield
    finally:
        await engine.stop()


app = FastAPI(title="Browser LLM Gateway", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/novnc", StaticFiles(directory=NOVNC_PATH), name="novnc")


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(DASHBOARD_PATH.read_text(encoding="utf-8"))


@app.get("/api/providers")
async def list_providers() -> dict[str, Any]:
    return {
        "providers": [
            provider.model_dump(mode="json")
            for provider in await engine.list_runtimes()
        ],
        "settings": {
            "port": settings.port,
            "default_model": settings.default_model,
        },
    }


@app.post("/api/providers")
async def add_provider(request: ProviderCreateRequest) -> dict[str, Any]:
    provider = store.upsert(request)
    return {"provider": provider.model_dump(mode="json")}


@app.post("/api/providers/{provider_name}/login")
async def login_provider(provider_name: str) -> dict[str, Any]:
    if not store.exists(provider_name):
        raise HTTPException(status_code=404, detail="Unknown provider")
    runtime = await engine.login(provider_name)
    return {"provider": runtime.model_dump(mode="json")}


@app.post("/api/providers/{provider_name}/session")
async def save_provider_session(provider_name: str) -> dict[str, Any]:
    if not store.exists(provider_name):
        raise HTTPException(status_code=404, detail="Unknown provider")
    runtime = await engine.save_session(provider_name)
    return {"provider": runtime.model_dump(mode="json")}


@app.post("/api/providers/{provider_name}/toggle")
async def toggle_provider(provider_name: str) -> dict[str, Any]:
    if not store.exists(provider_name):
        raise HTTPException(status_code=404, detail="Unknown provider")
    provider = store.toggle(provider_name)
    return {"provider": provider.model_dump(mode="json")}


@app.post("/api/providers/{provider_name}/delete")
async def delete_provider(provider_name: str) -> dict[str, Any]:
    if not store.exists(provider_name):
        raise HTTPException(status_code=404, detail="Unknown provider")
    store.delete(provider_name)
    return {"deleted": True}


@app.get("/api/settings/browser")
async def get_browser_settings() -> dict[str, Any]:
    return {
        "settings": load_browser_fingerprint_settings().model_dump(mode="json"),
        "options": {
            "timezones": list(COMMON_TIMEZONES),
            "user_agents": [
                {"label": label, "value": value}
                for label, value in USER_AGENT_PRESETS.items()
            ],
            "user_agent_platforms": USER_AGENT_PLATFORM_MAP,
        },
    }


@app.post("/api/settings/browser")
async def update_browser_settings(request: BrowserFingerprintSettings) -> dict[str, Any]:
    browser_settings = save_browser_fingerprint_settings(request)
    return {"settings": browser_settings.model_dump(mode="json")}


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    providers = await engine.list_runtimes()
    return {
        "object": "list",
        "data": [
            {
                "id": provider.name,
                "object": "model",
                "created": 0,
                "owned_by": "browser-llm-gateway",
                "status": provider.status,
            }
            for provider in providers
            if provider.enabled
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    model = request.model or settings.default_model
    if not store.exists(model):
        raise HTTPException(status_code=404, detail=f"Unknown model/provider: {model}")
    if not store.load(model).enabled:
        raise HTTPException(status_code=404, detail=f"Unknown model/provider: {model}")

    tools = request.tools or []
    if tools:
        prompt, new_chat, sent_hashes = _plan_prompt(model, request.messages, tools)
    else:
        _chat_state.pop(model, None)
        prompt = _messages_to_prompt(request.messages, tools)
    _debug_dump("last_prompt.txt", prompt)
    if tools:
        content = await engine.complete(model, prompt, new_chat=new_chat)
        _debug_dump("last_reply.txt", content)
        text, calls = _extract_tool_calls(content, tools)
        if not calls and "<tool_call" in (content or ""):
            retry = "[Your previous reply used an invalid tool_call format and was rejected. Re-emit the same call exactly in the specified format.]"
            content = await engine.complete(model, retry)
            _debug_dump("last_reply.txt", content)
            text, calls = _extract_tool_calls(content, tools)
        _chat_state[model] = sent_hashes
        if request.stream:
            return StreamingResponse(
                _fake_stream(model, text, calls),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
            )
        return JSONResponse(_completion_payload(model, text, calls))
    if request.stream:
        return StreamingResponse(
            _stream_openai_events(model, prompt),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    content = await engine.complete(model, prompt)
    created = int(time.time())
    return JSONResponse(
        {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }
    )


async def _stream_openai_events(model: str, prompt: str) -> AsyncIterator[str]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    initial = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    }
    yield _sse(initial)
    try:
        async for chunk in engine.stream(model, prompt):
            if not chunk:
                continue
            payload = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
            }
            yield _sse(payload)
        final = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield _sse(final)
        yield "data: [DONE]\n\n"
    except Exception as exc:  # noqa: BLE001 - stream errors need to reach clients.
        yield _sse({"error": {"message": str(exc), "type": "llm_bridge_error"}})
        yield "data: [DONE]\n\n"


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _start_vnc() -> None:
    if not VNC_SETUP_PATH.exists():
        raise RuntimeError(f"VNC setup script not found: {VNC_SETUP_PATH}")
    subprocess.run([str(VNC_SETUP_PATH)], cwd=str(BASE_DIR), check=True)


def _messages_to_prompt(messages: list[ChatMessage], tools: list[dict[str, Any]] | None = None) -> str:
    parts: list[str] = []
    if tools:
        parts.append(_tool_protocol(tools))
    for message in messages:
        content = _content_to_text(message.content)
        if message.role == "assistant" and message.tool_calls:
            for call in message.tool_calls:
                fn = call.get("function", {})
                args = fn.get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        args = json.loads(args, strict=False)
                    except json.JSONDecodeError:
                        pass
                block = "<tool_call>" + json.dumps(
                    {"name": fn.get("name"), "arguments": args}, ensure_ascii=False
                ) + "</tool_call>"
                content = f"{content}\n{block}" if content else block
        if not content:
            continue
        if message.role == "user":
            parts.append(content)
        elif message.role == "tool":
            parts.append(f"<tool_result>\n{content}\n</tool_result>")
        else:
            parts.append(f"{message.role}: {content}")
    if tools:
        parts.append(_REMINDER)
    return "\n\n".join(parts).strip()


def _content_to_text(content: str | list[dict[str, Any]] | None) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    chunks: list[str] = []
    for item in content:
        if item.get("type") == "text":
            chunks.append(str(item.get("text", "")))
    return "\n".join(chunks)


import re

_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)

_PROTOCOL_HEAD = """You are the reasoning engine of an autonomous coding agent. The agent can run tools for you.
To use a tool, write a block in EXACTLY this format:
<tool_call>{"name": "TOOL_NAME", "arguments": {"param": "value"}}</tool_call>

Rules:
1. To read, create or edit files, or to run commands, you MUST emit a tool_call block. Never just show code in a markdown block and tell the user to save it themselves.
2. You may emit several tool_call blocks in one reply. Put nothing after the last one.
3. "arguments" must be valid JSON matching the tool's parameter schema. Escape newlines inside strings as \\n.
4. Results arrive in the next message inside <tool_result> tags.
5. When the task is finished, or no tool is needed, answer in plain text with no tool_call block.

Available tools:
"""

_REMINDER = "[Reminder: respond with <tool_call> blocks to act on files or run commands. Do not paste code into markdown for the user to save. Reply in plain text only when the task is done.]"


def _tool_protocol(tools: list[dict[str, Any]]) -> str:
    lines = []
    for tool in tools:
        fn = tool.get("function", tool)
        desc = (fn.get("description") or "").strip()
        params = json.dumps(fn.get("parameters", {}), ensure_ascii=False)
        lines.append(f"- {fn.get('name', '')}: {desc}\n  parameters (JSON schema): {params}")
    return _PROTOCOL_HEAD + "\n".join(lines)


def _extract_tool_calls(content: str, tools: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    names = {tool.get("function", tool).get("name") for tool in tools}
    calls: list[dict[str, Any]] = []
    for raw in _TOOL_CALL_RE.findall(content or ""):
        try:
            obj = json.loads(raw, strict=False)
        except json.JSONDecodeError:
            continue
        name = obj.get("name") if isinstance(obj, dict) else None
        if name not in names:
            continue
        args = obj.get("arguments", obj.get("args", {}))
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        calls.append({
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {"name": name, "arguments": args},
        })
    text = _TOOL_CALL_RE.sub("", content or "").strip()
    return text, calls


def _completion_payload(model: str, text: str, calls: list[dict[str, Any]]) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    if calls:
        message["tool_calls"] = calls
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


async def _fake_stream(model: str, text: str, calls: list[dict[str, Any]]) -> AsyncIterator[str]:
    base = {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
    }

    def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
        return _sse({**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})

    yield chunk({"role": "assistant"})
    if text:
        yield chunk({"content": text})
    for i, call in enumerate(calls):
        yield chunk({"tool_calls": [{"index": i, **call}]})
    yield chunk({}, "tool_calls" if calls else "stop")
    yield "data: [DONE]\n\n"


def _debug_dump(name: str, text: str) -> None:
    try:
        (BASE_DIR / "data" / name).write_text(text or "", encoding="utf-8")
    except Exception:  # noqa: BLE001 - debug aid only
        pass


# ---------------- v2 overrides (later definitions win) ----------------
_CALL_RE = re.compile(r'<tool_call(?:\s+name="([^"]+)")?\s*>(.*?)</tool_call>', re.DOTALL)
_PARAM_RE = re.compile(r'<param\s+name="([^"]+)"\s*>(.*?)</param>', re.DOTALL)

_PROTOCOL_HEAD = """You are the reasoning engine of an autonomous coding agent. The agent can run tools for you.
To use a tool, write a block in EXACTLY this format, inside a fenced code block (three backticks followed by the word text) so the chat page does not reformat it:

```text
<tool_call name="TOOL_NAME">
<param name="PARAM_NAME">value</param>
</tool_call>
```

Rules:
1. To read, create or edit files, or to run commands, you MUST emit a tool_call block. Never show code in markdown and tell the user to save it.
2. Param values are RAW text. Do not escape quotes or newlines and do not use JSON for them. Write file contents exactly as they should appear in the file, verbatim, including underscores.
3. For array or object parameters, write a JSON value inside the param.
4. You may emit several tool_call blocks in one reply, all inside the fenced block. Write nothing after the last one.
5. Results arrive in the next message inside <tool_result> tags.
6. When the task is finished, or no tool is needed, answer in plain text with no tool_call block.

Available tools:
"""

_REMINDER = "[Reminder: act by emitting <tool_call name=...> blocks with <param> children, inside a text code fence. Param values are raw, unescaped. Do not paste code into markdown for the user to save. Reply in plain text only when the task is done.]"


def _render_call(name, args) -> str:
    if isinstance(args, str):
        try:
            args = json.loads(args, strict=False)
        except json.JSONDecodeError:
            args = {"input": args}
    if not isinstance(args, dict):
        args = {"input": args}
    rows = []
    for key, val in args.items():
        sval = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
        rows.append(f'<param name="{key}">{sval}</param>')
    return f'<tool_call name="{name}">\n' + "\n".join(rows) + "\n</tool_call>"


def _messages_to_prompt(messages: list[ChatMessage], tools: list[dict[str, Any]] | None = None) -> str:
    parts: list[str] = []
    if tools:
        parts.append(_tool_protocol(tools))
    for message in messages:
        content = _content_to_text(message.content)
        if message.role == "assistant" and message.tool_calls:
            for call in message.tool_calls:
                fn = call.get("function", {})
                block = _render_call(fn.get("name"), fn.get("arguments", "{}"))
                content = f"{content}\n{block}" if content else block
        if not content:
            continue
        if message.role == "user":
            parts.append(content)
        elif message.role == "tool":
            parts.append(f"<tool_result>\n{content}\n</tool_result>")
        else:
            parts.append(f"{message.role}: {content}")
    if tools:
        parts.append(_REMINDER)
    return "\n\n".join(parts).strip()


def _extract_tool_calls(content: str, tools: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    schemas: dict[str, dict] = {}
    for tool in tools:
        fn = tool.get("function", tool)
        schemas[fn.get("name")] = (fn.get("parameters") or {}).get("properties", {})
    calls: list[dict[str, Any]] = []
    for name, body in _CALL_RE.findall(content or ""):
        args = None
        params = _PARAM_RE.findall(body)
        if name and params:
            args = {}
            for key, val in params:
                if val.startswith("\n"):
                    val = val[1:]
                if val.endswith("\n"):
                    val = val[:-1]
                ptype = (schemas.get(name, {}).get(key) or {}).get("type")
                if ptype and ptype != "string":
                    try:
                        val = json.loads(val.strip())
                    except json.JSONDecodeError:
                        pass
                args[key] = val
        else:
            try:
                obj = json.loads(body.strip(), strict=False)
                name = obj.get("name") or name
                args = obj.get("arguments", obj.get("args", {}))
            except (json.JSONDecodeError, AttributeError):
                args = None
        if name not in schemas or args is None:
            print(f"[bridge] dropped tool call name={name!r} (unknown tool or unparsable)", flush=True)
            continue
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        calls.append({
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {"name": name, "arguments": args},
        })
    text = _CALL_RE.sub("", content or "")
    text = re.sub(r"```[a-zA-Z]*\s*```", "", text).strip()
    return text, calls


def _debug_dump(name: str, text: str) -> None:
    try:
        d = BASE_DIR / "data"
        (d / name).write_text(text or "", encoding="utf-8")
        with open(d / "bridge_log.txt", "a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%H:%M:%S')} {name} ({len(text or '')} chars) =====\n{text or ''}\n")
    except Exception:  # noqa: BLE001 - debug aid only
        pass


# ---------------- stateful chat tracking ----------------
import hashlib

# per provider: hashes of the messages already sent into the current web chat
_chat_state: dict[str, list[str]] = {}


def _note(text: str) -> None:
    print(f"[bridge] {text}", flush=True)
    _debug_dump("mode.txt", text)


def _msg_hash(m: ChatMessage) -> str:
    payload = json.dumps(
        [m.role, _content_to_text(m.content), m.tool_calls, m.tool_call_id],
        sort_keys=True, ensure_ascii=False, default=str,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _plan_prompt(model: str, messages: list[ChatMessage], tools: list[dict[str, Any]]) -> tuple[str, bool, list[str]]:
    hashes = [_msg_hash(m) for m in messages]
    prev = _chat_state.pop(model, None)  # stored again only after a successful reply
    full = _messages_to_prompt(messages, tools)
    if prev and len(hashes) > len(prev) and hashes[: len(prev)] == prev:
        delta = [m for m in messages[len(prev):] if m.role != "assistant"]
        body = _messages_to_prompt(delta, None)
        if body:
            prompt = "[Continuing the same task. New messages follow.]\n\n" + body + "\n\n" + _REMINDER
            _note(f"DELTA: sending {len(prompt)} chars instead of {len(full)} ({len(delta)} new messages)")
            return prompt, False, hashes
    if prev is None:
        reason = "no previous state"
    elif len(hashes) <= len(prev):
        reason = "history not longer than before"
    else:
        idx = next((i for i, (a, b) in enumerate(zip(hashes, prev)) if a != b), -1)
        reason = f"history differs at message {idx}"
    _note(f"FULL+NEW CHAT: sending {len(full)} chars ({reason})")
    return full, True, hashes
