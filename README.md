# Browser LLM Gateway

A local, OpenAI-style API gateway that drives web-based LLM chat interfaces with Playwright. It lets local applications and coding agents send chat-completion requests to supported browser chat sites through a single HTTP endpoint.

> **Important:** This project automates third-party websites; it is not an official API client for those providers. Website UI changes, login requirements, rate limits, account restrictions, and provider terms can affect reliability. Use it only with accounts and services you are authorized to access.

## Contents

- [How it works](#how-it-works)
- [Features and provider status](#features-and-provider-status)
- [Requirements](#requirements)
- [Installation and startup](#installation-and-startup)
- [Authentication and network exposure](#authentication-and-network-exposure)
- [Dashboard](#dashboard)
- [API reference](#api-reference)
- [Persistent conversations](#persistent-conversations)
- [Tool calling](#tool-calling)
- [Provider configuration](#provider-configuration)
- [Browser fingerprint settings](#browser-fingerprint-settings)
- [Configuration reference](#configuration-reference)
- [Data, sessions, and lifecycle](#data-sessions-and-lifecycle)
- [Security model and limitations](#security-model-and-limitations)
- [Troubleshooting](#troubleshooting)
- [Development and tests](#development-and-tests)
- [Project layout](#project-layout)
- [Roadmap and known limitations](#roadmap-and-known-limitations)

## How it works

```text
OpenAI-compatible client
        |
        | HTTP / JSON / SSE
        v
FastAPI gateway (127.0.0.1:9920)
        |
        | Playwright
        v
Provider website in Chromium
        |
        v
Assistant response extracted from the page
```

The gateway translates incoming chat messages into a prompt for the selected provider, submits it through a managed Chromium page, and extracts the assistant response. It exposes the result using OpenAI-style response objects.

This is browser automation rather than a direct provider API integration. The gateway does not provide the same guarantees, latency, structured metadata, or compatibility as an official model API.

## Features and provider status

- OpenAI-style `GET /v1/models` and `POST /v1/chat/completions` endpoints.
- Non-streaming JSON responses and Server-Sent Events (SSE) streaming.
- Built-in provider configurations for ChatGPT, Gemini, Claude, and DeepSeek.
- DOM-based response extraction, with optional network-assisted provider mode.
- Persistent provider-level browser conversations for tool-enabled clients, even when they do not send a conversation ID.
- Optional isolated conversations using `X-Bridge-Conversation-ID`; requests without tools and without this header remain stateless.
- Tool schema serialization and best-effort extraction of tool calls from model output.
- Local management dashboard for providers, browser login sessions, and browser fingerprint settings.
- Bearer-token authentication for API and management endpoints.
- Provider URL validation and browser request filtering intended to reduce server-side request forgery (SSRF) risk.

Provider configurations are not a guarantee that a site is currently usable. Some providers may require login, present human verification, change their page structure, or block automation. Claude and DeepSeek are included but disabled by default in the checked-in configuration; enable and test them from the dashboard if appropriate.

## Requirements

- Python 3.10 or newer.
- A supported desktop/server environment for Playwright Chromium.
- Internet access to the provider websites.
- A browser account/session for providers that require authentication.

On non-macOS systems, startup also invokes the bundled VNC setup script and expects the relevant X11/VNC/noVNC system programs to be installed. macOS uses the local desktop display and skips that Linux-oriented VNC startup.

## Installation and startup

### 1. Clone the repository

```bash
git clone https://github.com/usman-faisal-dev/browser-llm-gateway.git
cd browser-llm-gateway
```

### 2. Create a virtual environment and install dependencies

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

Windows PowerShell (the VNC startup path is intended for non-macOS Unix-like systems):

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

### 3. Set an API key

The server fails closed when `LLM_BRIDGE_API_KEY` is empty. Generate a random key and keep it private:

```bash
export LLM_BRIDGE_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

You can also put the setting in a local `.env` file (which is ignored by Git):

```dotenv
LLM_BRIDGE_API_KEY=replace-with-a-long-random-secret
LLM_BRIDGE_HEADLESS=false
```

Do not commit `.env`, API keys, browser profiles, or saved login sessions.

### 4. Start the server

```bash
uvicorn app.main:app --host 127.0.0.1 --port 9920
```

The default bind address is loopback. Keep it that way unless you have deliberately designed and secured a remote-access setup.

For headless Chromium:

```bash
LLM_BRIDGE_HEADLESS=true uvicorn app.main:app --host 127.0.0.1 --port 9920
```

### 5. Open the dashboard

Visit [http://127.0.0.1:9920/](http://127.0.0.1:9920/). The dashboard loads without a bearer token, but its API requests prompt for the key and send it in the Authorization header. The key is kept in that browser tab's session storage.

## Authentication and network exposure

All API and management requests require:

```http
Authorization: Bearer YOUR_API_KEY
```

The server returns:

- `503` if the API key has not been configured.
- `401` if the bearer token is missing or invalid.

The dashboard HTML at `GET /`, preflight `OPTIONS` requests, and static `/novnc/` assets are exceptions to the API-key middleware. Do not treat the dashboard page or noVNC assets as authenticated resources.

CORS is restricted to the local dashboard origins `http://127.0.0.1:9920` and `http://localhost:9920`, with credentials disabled. CORS is not a substitute for authentication.

The VNC server and websockify are configured to bind to `127.0.0.1`; x11vnc is started without a VNC password because it is loopback-only. **Do not expose VNC/noVNC ports directly to an untrusted network.** If remote access is required, use a carefully secured tunnel and ensure the dashboard/noVNC paths are not unintentionally exposed.

## Dashboard

The dashboard is available at `http://127.0.0.1:9920/`. It provides controls to:

- View configured providers and their runtime status.
- Open a provider page so you can sign in or complete any required verification.
- Save a provider browser session.
- Enable, disable, or delete provider configurations.
- Add a provider using its URL and CSS selectors.
- Adjust browser fingerprint settings.
- Send a test prompt through the built-in chat interface.

The dashboard is a local management interface, not a multi-user administration system.

## API reference

Base URL:

```text
http://127.0.0.1:9920
```

### `GET /v1/models`

Returns enabled providers as model IDs. Use the provider's `name` as the `model` value in chat requests.

```bash
curl http://127.0.0.1:9920/v1/models \
  -H "Authorization: Bearer $LLM_BRIDGE_API_KEY"
```

### `POST /v1/chat/completions`

Accepts a JSON request with the following supported fields:

| Field | Type | Description |
|---|---|---|
| `model` | string | Provider name, such as `chatgpt` or `gemini`. If omitted, uses `LLM_BRIDGE_DEFAULT_MODEL`. |
| `messages` | array | Conversation messages. Roles supported by the request model: `system`, `user`, `assistant`, and `tool`. |
| `stream` | boolean | When true, returns Server-Sent Events. Defaults to false. |
| `tools` | array | Optional function tool definitions. See [Tool calling](#tool-calling). |
| `temperature` | number | Accepted by the request model; not currently mapped to a provider UI setting. |
| `max_tokens` | number | Accepted by the request model; not currently enforced by the provider UI. |
| `tool_choice` | any | Accepted by the request model; provider-side tool selection is not currently enforced. |

Example non-streaming request:

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

Example streaming request:

```bash
curl -N http://127.0.0.1:9920/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LLM_BRIDGE_API_KEY" \
  -d '{
    "model": "chatgpt",
    "messages": [
      {"role": "user", "content": "Write a short poem about coding."}
    ],
    "stream": true
  }'
```

Streaming responses use the `text/event-stream` media type and OpenAI-style `data: {...}` chunks, ending with `data: [DONE]`. The current stream is based on response extraction from the browser page; it is not a guarantee of direct token-level provider network streaming.

### Management endpoints

These endpoints also require bearer authentication:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/providers` | List provider configurations and runtime status. |
| `POST` | `/api/providers` | Create or replace a provider configuration. |
| `POST` | `/api/providers/{name}/login` | Open/reinitialize the provider browser page. |
| `POST` | `/api/providers/{name}/session` | Save the provider browser session. |
| `POST` | `/api/providers/{name}/toggle` | Enable or disable a provider. |
| `POST` | `/api/providers/{name}/delete` | Delete a provider configuration. |
| `GET` | `/api/settings/browser` | Read browser fingerprint settings and available presets. |
| `POST` | `/api/settings/browser` | Validate and save browser fingerprint settings. |

Unknown or disabled providers return HTTP `404`. A malformed conversation ID returns HTTP `400`. If another request is already running for the same persistent conversation, the gateway returns HTTP `409`.

## Persistent conversations

Tool-enabled requests without `X-Bridge-Conversation-ID` reuse one default persistent conversation per provider. This restores the original provider-level behavior for clients such as agentic coding tools that send function tools but do not provide a conversation identifier. The gateway tracks the transcript and sends only the new portion when the supplied history extends the previous successful request.

Because this default is shared by all clients using the same provider, only one request can run in it at a time; overlapping requests receive HTTP `409`. Clients that need independent or concurrent conversations should send their own unique `X-Bridge-Conversation-ID`.

Requests without tools and without a conversation ID remain stateless and start a fresh provider chat for each request. Explicit conversation IDs enable isolated persistent conversations whether or not tools are supplied.

To keep a specific provider chat isolated across requests, send the optional header:

```http
X-Bridge-Conversation-ID: unique-client-conversation-id
```

Example:

```bash
curl http://127.0.0.1:9920/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LLM_BRIDGE_API_KEY" \
  -H "X-Bridge-Conversation-ID: demo-task-001" \
  -d '{
    "model": "chatgpt",
    "messages": [
      {"role": "user", "content": "Remember this test code: ORCHID-731. Reply only ACK."}
    ]
  }'
```

For the next turn, use the **same provider and same ID**, and send the updated transcript:

```bash
curl http://127.0.0.1:9920/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LLM_BRIDGE_API_KEY" \
  -H "X-Bridge-Conversation-ID: demo-task-001" \
  -d '{
    "model": "chatgpt",
    "messages": [
      {"role": "user", "content": "Remember this test code: ORCHID-731. Reply only ACK."},
      {"role": "assistant", "content": "ACK"},
      {"role": "user", "content": "What was the test code?"}
    ]
  }'
```

The gateway uses the client-supplied transcript to detect new messages and synchronize only the new portion when the existing provider page is still available. If the transcript is changed or shortened, or the provider page is missing, it rebuilds the provider chat from the supplied history.

Conversation IDs:

- Must contain 1–128 printable non-space characters.
- Are scoped by provider. The same ID used with two different providers creates two independent conversations.
- Should be unique per client task/conversation. Do not use one shared ID for unrelated tasks.
- Must be sent on every request that belongs to the persistent conversation.
- Are not authentication tokens or secrets.

The conversation manager and conversation pages are in memory. Restarting the gateway closes browser pages and clears its conversation checkpoints. To recover, the client must send the full transcript again with the same ID; the gateway will rebuild the provider conversation. Concurrent requests using the same provider and conversation ID are rejected with HTTP `409`; use separate IDs for independent concurrent tasks.

## Tool calling

The chat endpoint accepts OpenAI-style function tool definitions in the `tools` array. The gateway serializes the tool descriptions and parameter schemas into an instruction for the browser model, then attempts to parse tool-call markup from the model's response into OpenAI-style `tool_calls`.

This is best-effort prompt-based tool calling, not native provider function calling. The model must follow the gateway's required output format; malformed or unsupported output may not be recognized. The client remains responsible for executing the returned tool calls and sending the results back as messages with role `tool` (including the corresponding `tool_call_id`) in a subsequent request. Use the same conversation ID for each step if persistent mode is enabled.

Tool-call streaming currently emits the parsed result after the provider response has been collected; it is not token-by-token streaming of tool-call arguments. Tool parsing and schema validation remain areas for improvement.

## Provider configuration

Provider definitions are stored as JSON files in `data/providers/`. The filename should match the provider `name`, for example `data/providers/custom-model.json`.

A minimal DOM-mode provider configuration:

```json
{
  "name": "custom-model",
  "display_name": "Custom Model",
  "url": "https://example.com/chat",
  "enabled": true,
  "mode": "dom",
  "input_selector": "textarea#prompt",
  "send_button_selector": "button#submit",
  "response_selector": ".message-assistant:last-child",
  "response_text_selector": ".message-content",
  "response_complete_selector": ".message-assistant[data-complete='true']"
}
```

Configuration fields:

| Field | Required | Description |
|---|---:|---|
| `name` | Yes | API model ID and JSON filename stem. Letters, numbers, underscores, and hyphens only. |
| `display_name` | Yes | Human-readable name shown in the dashboard. |
| `url` | Yes | Public HTTP or HTTPS provider page URL. |
| `enabled` | No | Whether the provider is available; defaults to true. |
| `mode` | No | `dom` or `network`; defaults to `dom`. |
| `input_selector` | Yes | CSS selector for the message input. |
| `send_button_selector` | No | CSS selector for the send button. |
| `response_selector` | Yes | CSS selector matching assistant response elements. |
| `response_text_selector` | No | Optional selector for the text container within the latest response. |
| `response_complete_selector` | No | Optional selector indicating that the assistant response is complete. |
| `stream_url_pattern` | No | Optional URL substring/pattern used by network-assisted extraction. |
| `user_data_dir` | No | Optional browser profile directory setting in the provider model. |

Selectors are specific to each website and can break when the provider changes its frontend. Inspect the current page DOM and test selectors before relying on a custom provider.

The dashboard/API provider-creation form currently exposes the main URL and DOM selector fields. For advanced fields such as `mode` and `stream_url_pattern`, edit the provider JSON file directly and restart/reload the relevant provider as needed.

### Built-in provider files

| API model ID | Website | Default enabled |
|---|---|---:|
| `chatgpt` | ChatGPT | Yes |
| `gemini` | Gemini | Yes |
| `claude` | Claude | No |
| `deepseek` | DeepSeek | No |

The provider site may require you to sign in using the dashboard's browser session before chat requests will work.

## Browser fingerprint settings

The dashboard's browser settings endpoint controls the browser context's:

- User-Agent string.
- Additional HTTP headers.
- Locale.
- Timezone.
- Platform value.
- WebDriver property masking toggle.

These settings are intended for browser compatibility and configuration. They do not guarantee that a website will accept automation or bypass its bot detection. Provider websites may still require human verification or block automated access.

## Configuration reference

Settings use the `LLM_BRIDGE_` environment-variable prefix and can also be loaded from a local `.env` file.

| Variable | Default | Purpose |
|---|---|---|
| `LLM_BRIDGE_HOST` | `127.0.0.1` | Configured host setting. The recommended local bind is loopback. |
| `LLM_BRIDGE_PORT` | `9920` | Configured port setting. |
| `LLM_BRIDGE_DEFAULT_MODEL` | `chatgpt` | Provider used when a request omits `model`. |
| `LLM_BRIDGE_API_KEY` | Empty | Required bearer token. Server returns 503 when unset. |
| `LLM_BRIDGE_HEADLESS` | `false` | Run Chromium without a visible browser window. |
| `LLM_BRIDGE_RESPONSE_IDLE_SECONDS` | `1.5` | Response idle timing setting. |
| `LLM_BRIDGE_RESPONSE_TIMEOUT_SECONDS` | `180` | Maximum response wait setting. |
| `LLM_BRIDGE_USER_AGENT` | Built-in Chrome UA | Default browser User-Agent. |
| `LLM_BRIDGE_DISABLE_WEBDRIVER` | `true` | Enable/disable WebDriver property masking. |
| `LLM_BRIDGE_LOCALE` | `en-US` | Default browser locale. |
| `LLM_BRIDGE_TIMEZONE` | `America/New_York` | Default browser timezone. |
| `LLM_BRIDGE_PLATFORM` | `Win32` | Default browser platform value. |

The settings model also supports `LLM_BRIDGE_EXTRA_HEADERS` as a JSON object. Browser fingerprint settings saved through the dashboard are stored separately in `data/browser_settings.json` and take precedence for the browser context.

## Data, sessions, and lifecycle

Runtime data is stored under `data/`:

- `data/providers/`: provider configuration JSON files. These are application configuration and may be version-controlled.
- `data/sessions/`: saved browser storage state, including authentication cookies/tokens. Treat as sensitive.
- `data/browser/`: persistent browser profile data. Treat as sensitive.
- `data/browser_settings.json`: dashboard-saved browser fingerprint settings.
- `data/last_prompt.txt` and `data/last_reply.txt`: diagnostic prompt/response snapshots written by the current request path. These may contain conversation content; protect them and clear them when no longer needed.
- `data/vnc/`: VNC/noVNC process logs and PID files on supported non-macOS startup.

Session and browser-profile directories, `.env`, and browser settings are excluded from Git by `.gitignore`. Back up private runtime data securely if you need to preserve login sessions. Do not share it in bug reports.

On shutdown, the browser engine attempts to save provider browser state and closes its managed contexts. Persistent conversation pages are runtime state and are not restored as active conversations after a process restart.

## Security model and limitations

Security controls currently implemented include:

- Bearer-token authentication for API and management routes, with constant-time token comparison.
- Fail-closed behavior when no API key is configured.
- CORS restricted to the local dashboard origins, with credentials disabled.
- Loopback binding for the VNC and websockify services.
- HTTP/HTTPS-only provider URLs; embedded URL credentials, local hostnames, private/reserved IP literals, and non-public DNS answers are rejected.
- Browser HTTP request routing validates destinations, including redirects and subresources, to reduce access to private/local network targets.
- Chromium is launched without the `--no-sandbox` flag.

Important limitations:

- This is a local development tool, not a hardened multi-tenant service. Do not expose it directly to the public internet.
- The browser DNS preflight and Playwright HTTP routing are defense in depth, not a complete network egress boundary. WebSocket traffic is not covered by the same Playwright HTTP routing. Use host/container/firewall egress restrictions if the environment requires strong network isolation.
- The dashboard root and noVNC static assets are not protected by the API-key middleware. Keep the service bound to loopback and secure any tunnel or proxy separately.
- Provider browser profiles and session files can contain account credentials and must be protected.
- The API key protects gateway endpoints; it does not protect provider accounts from website-side security controls or misuse.
- Browser automation can be unreliable and may stop working after provider website changes.

## Troubleshooting

### API returns HTTP 503

Set `LLM_BRIDGE_API_KEY` in the same environment used to start Uvicorn, then restart the server.

### API returns HTTP 401

Check that the request includes the exact bearer token:

```http
Authorization: Bearer YOUR_API_KEY
```

If using the dashboard, refresh the page and enter the current key when prompted.

### Provider returns 404

Check that the `model` value matches a provider name in `data/providers/` and that the provider is enabled.

### Provider cannot respond or times out

1. Open the dashboard and launch the provider.
2. Check whether the provider requires login, CAPTCHA, or human verification.
3. Save the provider session after signing in.
4. Confirm the provider URL and selectors still match the live page.
5. Check the Uvicorn terminal output for browser or selector errors.

### Persistent conversation starts over

Confirm that every request in the same conversation uses the same `X-Bridge-Conversation-ID` and provider name. Confirm that the client sends the full, correctly ordered transcript. If the gateway restarted or the provider tab was closed, the gateway must rebuild from the transcript.

### Same conversation returns HTTP 409

A request is already in progress for that provider/conversation ID. Wait for it to finish before sending another request with the same ID. Use a different ID for an independent task.

### Browser cannot reach a configured provider URL

The gateway intentionally rejects private/local destinations and hostnames that resolve to non-public addresses. A local development server or private-network provider is not accepted by the current URL security policy.

## Development and tests

Activate the virtual environment, install dependencies, and run the test suite from the repository root:

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

Run the local API:

```bash
export LLM_BRIDGE_API_KEY="your-local-development-key"
uvicorn app.main:app --host 127.0.0.1 --port 9920
```

The automated tests cover core API/security behavior, URL validation, conversation-manager logic, and browser-engine behavior using test doubles/mocks where appropriate. Passing unit tests does not guarantee that a live third-party provider website still works; perform a live browser smoke test after changing provider selectors or browser automation behavior.

## Project layout

```text
├── app/
│   ├── main.py                 # FastAPI routes, prompt serialization, tool-call parsing
│   ├── browser_engine.py       # Playwright lifecycle, provider pages, DOM/network extraction
│   ├── conversation_manager.py # Client-ID conversation checkpoints and recovery state
│   ├── models.py               # Pydantic request and provider models
│   ├── provider_store.py       # JSON provider configuration persistence
│   ├── settings.py             # Environment and browser fingerprint settings
│   ├── url_security.py         # Provider URL, DNS, and destination validation
│   ├── dashboard.html          # Local management dashboard
│   └── vnc_setup.sh            # Non-macOS Xvfb/VNC/noVNC startup
├── data/
│   ├── providers/              # Provider configuration files
│   ├── sessions/               # Saved browser storage state (private runtime data)
│   └── browser/                # Persistent browser profiles (private runtime data)
├── tests/                      # Automated tests
├── novnc/                      # noVNC static assets
├── requirements.txt            # Python dependencies
└── README.md
```

## Roadmap and known limitations

The following are not yet complete:

- More robust tool-call parsing and JSON Schema validation.
- Broader OpenAI API compatibility and more accurate token usage reporting.
- Dedicated API integration and concurrency test coverage.
- Structured logging, health/readiness checks, and continuous integration.
- Conversation expiration and resource cleanup policies for long-running processes.
- Direct provider network streaming and additional provider-specific adapters.

The gateway currently keeps conversation checkpoints in process memory. Restart recovery relies on the client resending its transcript; durable conversation storage and conversation cleanup policies are future work.
