# Browser LLM Gateway

A local gateway that bridges web-based LLM chat interfaces into a standardized, OpenAI-compatible REST API (`/v1/chat/completions` and `/v1/models`).

Using Playwright in the background, the bridge automates web chat pages to provide an API endpoint for local development, coding agents, and applications without requiring paid API subscriptions.

```text
Client Application / Agent  ──>  OpenAI REST API  ──>  Browser LLM Gateway  ──>  Browser Chat UI
```

---

## Features

- **OpenAI Compatible**: Seamlessly works with tools expecting `/v1/chat/completions` and `/v1/models`.
- **Streaming & Non-Streaming**: Supports standard JSON responses as well as Server-Sent Events (SSE).
- **Built-in Providers**: Preconfigured support for:
  - ChatGPT
  - Claude
  - Gemini
  - DeepSeek
- **Extensible via Selectors**: Add new web chat interfaces simply by adding a JSON selector config to `data/providers/`.
- **Tool Calling Support**: Automatically serializes tool schemas and parses model outputs into OpenAI-style `tool_calls` for autonomous agents.
- **Conversation Isolation**: Each API request starts a fresh provider chat using the caller-supplied transcript, preventing cross-client history leakage.
- **Browser Stealth & Fingerprints**: Configurable user-agents, headers, timezones, and anti-detection scripts.
- **Management Dashboard**: Built-in web UI (`http://127.0.0.1:9920/`) to view, configure, and manage provider sessions.

---

## Quick Start

### 1. Set Up Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure authentication and run the bridge

Set a long, random API key before starting the server. All dashboard, management,
and OpenAI-compatible API routes require this bearer token.

```bash
export LLM_BRIDGE_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uvicorn app.main:app --host 127.0.0.1 --port 9920
```

The dashboard prompts for the key and keeps it in the current browser tab's
session storage. API clients must send `Authorization: Bearer <key>`.
The server refuses requests with HTTP 503 if no key is configured.

VNC and noVNC listen on loopback only. For remote access, use an authenticated
SSH tunnel or a trusted reverse proxy; do not expose ports 5900/6080 directly.

> **Tip (Headless Mode):** To run Chromium in the background without visible windows:
> ```bash
> LLM_BRIDGE_HEADLESS=true uvicorn app.main:app --host 127.0.0.1 --port 9920
> ```

### 3. Open the Dashboard

Navigate to `http://127.0.0.1:9920/` in your browser. From here you can:
- Open browser sessions to log into accounts (ChatGPT, Claude, Gemini, etc.).
- Save sessions so cookies and login tokens persist across restarts.
- Test conversations directly in the built-in Chat view.
- Customize browser fingerprinting settings.

---

## API Usage

The local API is accessible at `http://127.0.0.1:9920`.

### List Models

```bash
curl http://127.0.0.1:9920/v1/models -H "Authorization: Bearer $LLM_BRIDGE_API_KEY"
```

### Chat Completion (Non-Streaming)

```bash
curl http://127.0.0.1:9920/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LLM_BRIDGE_API_KEY" \
  -d '{
    "model": "chatgpt",
    "messages": [
      {"role": "user", "content": "Explain quantum computing in one sentence."}
    ]
  }'
```

### Chat Completion (Streaming)

```bash
curl http://127.0.0.1:9920/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "chatgpt",
    "messages": [
      {"role": "user", "content": "Write a short poem about coding."}
    ],
    "stream": true
  }'
```

---

## Adding Custom Providers

You can add any web-based chat interface by creating a JSON file inside `data/providers/<provider_name>.json`:

```json
{
  "name": "custom-model",
  "display_name": "Custom Model",
  "url": "https://example.com/chat",
  "mode": "dom",
  "input_selector": "textarea#prompt",
  "send_button_selector": "button#submit",
  "response_selector": ".message-assistant:last-child"
}
```

Once saved, the provider becomes immediately available through the API as `custom-model`.

---

## Project Structure

```text
├── app/
│   ├── main.py              # FastAPI endpoints & tool-calling protocol
│   ├── browser_engine.py    # Playwright browser manager & DOM stream scraping
│   ├── models.py            # Pydantic models for API and providers
│   ├── provider_store.py    # JSON provider persistence
│   ├── settings.py          # App configuration and browser fingerprint presets
│   └── dashboard.html       # Web management interface
├── data/
│   ├── providers/           # Provider configuration files (*.json)
│   ├── sessions/            # Persisted browser session state (ignored by git)
│   └── browser/             # Persistent browser user data dirs (ignored by git)
├── tests/                   # Unit test suite
└── requirements.txt         # Python dependencies
```
