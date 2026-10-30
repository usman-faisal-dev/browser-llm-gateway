from __future__ import annotations

import asyncio
import json
import shutil
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import BrowserContext, Error, Page, Playwright, Request, Route, async_playwright

from app.models import ProviderConfig, ProviderMode, ProviderRuntime, ProviderStatus
from app.provider_store import ProviderStore
from app.url_security import validate_public_http_url_async
from app.settings import (
    BROWSER_DIR,
    DEFAULT_USER_AGENT,
    SESSIONS_DIR,
    BrowserFingerprintSettings,
    default_extra_headers,
    ensure_data_dirs,
    load_browser_fingerprint_settings,
    settings,
)


CLOSED_BROWSER_ERROR = "Target page, context or browser has been closed"
BROWSER_VIEWPORT = {"width": 1920, "height": 1080}
CHROME_124_USER_AGENT = DEFAULT_USER_AGENT
HUMAN_LIKE_HEADERS = default_extra_headers()
WEBDRIVER_INIT_SCRIPT = "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
STEALTH_INIT_SCRIPT = """
(() => {
  const defineGetter = (target, property, getter) => {
    try {
      Object.defineProperty(target, property, {
        configurable: true,
        enumerable: true,
        get: getter,
      });
    } catch (error) {
      // Some browser properties are non-configurable in older Chromium builds.
    }
  };

  defineGetter(Navigator.prototype, "webdriver", () => undefined);

  if (!window.chrome) {
    Object.defineProperty(window, "chrome", {
      configurable: true,
      enumerable: true,
      writable: true,
      value: {},
    });
  }

  const chromeRuntime = {
    PlatformOs: {
      MAC: "mac",
      WIN: "win",
      ANDROID: "android",
      CROS: "cros",
      LINUX: "linux",
      OPENBSD: "openbsd",
    },
    PlatformArch: {
      ARM: "arm",
      ARM64: "arm64",
      X86_32: "x86-32",
      X86_64: "x86-64",
      MIPS: "mips",
      MIPS64: "mips64",
    },
    PlatformNaclArch: {
      ARM: "arm",
      X86_32: "x86-32",
      X86_64: "x86-64",
      MIPS: "mips",
      MIPS64: "mips64",
    },
    RequestUpdateCheckStatus: {
      THROTTLED: "throttled",
      NO_UPDATE: "no_update",
      UPDATE_AVAILABLE: "update_available",
    },
    OnInstalledReason: {
      INSTALL: "install",
      UPDATE: "update",
      CHROME_UPDATE: "chrome_update",
      SHARED_MODULE_UPDATE: "shared_module_update",
    },
    OnRestartRequiredReason: {
      APP_UPDATE: "app_update",
      OS_UPDATE: "os_update",
      PERIODIC: "periodic",
    },
  };
  Object.defineProperty(window.chrome, "runtime", {
    configurable: true,
    enumerable: true,
    writable: true,
    value: chromeRuntime,
  });
  if (typeof window.chrome.csi !== "function") {
    Object.defineProperty(window.chrome, "csi", {
      configurable: true,
      enumerable: true,
      writable: true,
      value: () => ({
        onloadT: Date.now(),
        startE: Date.now(),
        pageT: Date.now() - performance.timeOrigin,
        tran: 15,
      }),
    });
  }
  if (typeof window.chrome.loadTimes !== "function") {
    Object.defineProperty(window.chrome, "loadTimes", {
      configurable: true,
      enumerable: true,
      writable: true,
      value: () => ({
        requestTime: performance.timeOrigin / 1000,
        startLoadTime: performance.timeOrigin / 1000,
        commitLoadTime: performance.timeOrigin / 1000,
        finishDocumentLoadTime: Date.now() / 1000,
        finishLoadTime: Date.now() / 1000,
        firstPaintTime: Date.now() / 1000,
        firstPaintAfterLoadTime: 0,
        navigationType: "Other",
        wasFetchedViaSpdy: true,
        wasNpnNegotiated: true,
        npnNegotiatedProtocol: "h2",
        wasAlternateProtocolAvailable: false,
        connectionInfo: "h2",
      }),
    });
  }

  const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
  if (originalQuery) {
    window.navigator.permissions.query = (parameters) => {
      const name = parameters && parameters.name;
      if (name === "notifications") {
        return Promise.resolve({ state: Notification.permission });
      }
      return originalQuery.call(window.navigator.permissions, parameters);
    };
  }

  const pluginNames = [
    "PDF Viewer",
    "Chrome PDF Viewer",
    "Chromium PDF Viewer",
    "Microsoft Edge PDF Viewer",
    "WebKit built-in PDF",
  ];
  const mimeTypes = [
    { type: "application/pdf", suffixes: "pdf", description: "Portable Document Format" },
    { type: "text/pdf", suffixes: "pdf", description: "Portable Document Format" },
  ];
  const plugins = pluginNames.map((name) => ({
    name,
    filename: "internal-pdf-viewer",
    description: "Portable Document Format",
    length: mimeTypes.length,
    0: mimeTypes[0],
    1: mimeTypes[1],
    item: (index) => mimeTypes[index] || null,
    namedItem: (type) => mimeTypes.find((mimeType) => mimeType.type === type) || null,
  }));
  defineGetter(Navigator.prototype, "plugins", () => ({
    length: plugins.length,
    0: plugins[0],
    1: plugins[1],
    2: plugins[2],
    3: plugins[3],
    4: plugins[4],
    item: (index) => plugins[index] || null,
    namedItem: (name) => plugins.find((plugin) => plugin.name === name) || null,
    refresh: () => undefined,
    [Symbol.iterator]: function* () {
      yield* plugins;
    },
  }));
  defineGetter(Navigator.prototype, "mimeTypes", () => ({
    length: mimeTypes.length,
    0: mimeTypes[0],
    1: mimeTypes[1],
    item: (index) => mimeTypes[index] || null,
    namedItem: (type) => mimeTypes.find((mimeType) => mimeType.type === type) || null,
    [Symbol.iterator]: function* () {
      yield* mimeTypes;
    },
  }));

  const languages = __BROWSER_LANGUAGES__;
  defineGetter(Navigator.prototype, "language", () => languages[0]);
  defineGetter(Navigator.prototype, "languages", () => languages.slice());
})();
""".strip()


@dataclass
class ProviderHandle:
    config: ProviderConfig
    context: BrowserContext
    page: Page
    lock: asyncio.Lock
    status: ProviderStatus = ProviderStatus.disconnected
    error: str | None = None


class BrowserEngine:
    def __init__(self, store: ProviderStore) -> None:
        self.store = store
        self._playwright: Playwright | None = None
        self._handles: dict[str, ProviderHandle] = {}
        self._engine_lock = asyncio.Lock()

    async def start(self) -> None:
        ensure_data_dirs()
        if self._playwright is None:
            self._playwright = await async_playwright().start()

    async def stop(self) -> None:
        for handle in list(self._handles.values()):
            if self._is_handle_alive(handle):
                await self._save_session(handle)
            await self._close_context(handle.context)
        self._handles.clear()
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def list_runtimes(self) -> list[ProviderRuntime]:
        runtimes: list[ProviderRuntime] = []
        for config in self.store.list():
            handle = self._handles.get(config.name)
            runtimes.append(self._runtime(config, handle))
        return runtimes

    async def login(self, provider_name: str) -> ProviderRuntime:
        handle = await self._create_fresh_handle(provider_name)
        async with handle.lock:
            handle.status = ProviderStatus.disconnected
            try:
                handle = await self._goto_provider(handle)
                handle.status = ProviderStatus.connected
                handle.error = None
            except Exception as exc:  # noqa: BLE001 - surfaced in dashboard and API.
                handle.status = ProviderStatus.error
                handle.error = str(exc)
            return self._runtime(handle.config, handle)

    async def save_session(self, provider_name: str) -> ProviderRuntime:
        handle = await self._get_or_create(provider_name)
        try:
            await self._save_session(handle)
            handle.status = ProviderStatus.connected
            handle.error = None
        except Exception as exc:  # noqa: BLE001 - surfaced in dashboard and API.
            handle.status = ProviderStatus.error
            handle.error = str(exc)
        return self._runtime(handle.config, handle)

    async def complete(self, provider_name: str, prompt: str, new_chat: bool = False) -> str:
        chunks: list[str] = []
        async for chunk in self.stream(provider_name, prompt, new_chat):
            chunks.append(chunk)
        return "".join(chunks)

    async def stream(self, provider_name: str, prompt: str, new_chat: bool = False) -> AsyncIterator[str]:
        handle = await self._get_or_create(provider_name)
        async with handle.lock:
            handle.status = ProviderStatus.busy
            handle.error = None
            try:
                if new_chat:
                    handle = await self._goto_provider(handle)
                    print(f"[bridge] new chat opened: {handle.page.url}", flush=True)
                if handle.config.mode == ProviderMode.network:
                    async for chunk in self._network_then_dom_stream(handle, prompt):
                        yield chunk
                else:
                    async for chunk in self._dom_stream(handle, prompt):
                        yield chunk
                print(f"[bridge] chat url after reply: {handle.page.url}", flush=True)
                handle = self._handles.get(provider_name, handle)
                handle.status = ProviderStatus.connected
                await self._save_session(handle)
            except Exception as exc:  # noqa: BLE001 - surfaced to caller.
                handle.status = ProviderStatus.error
                handle.error = str(exc)
                raise

    async def _get_or_create(self, provider_name: str) -> ProviderHandle:
        async with self._engine_lock:
            handle = self._handles.get(provider_name)
            if handle is not None:
                if self._is_handle_alive(handle):
                    return handle
                await self._discard_handle(provider_name, handle)
            await self.start()
            return await self._create_handle(provider_name)

    async def _create_fresh_handle(self, provider_name: str) -> ProviderHandle:
        async with self._engine_lock:
            handle = self._handles.get(provider_name)
            if handle is not None:
                await self._discard_handle(provider_name, handle)
            await self.start()
            return await self._create_handle(provider_name)

    async def _create_handle(self, provider_name: str) -> ProviderHandle:
        config = self.store.load(provider_name)
        assert self._playwright is not None
        user_data_dir = self._user_data_dir(config)
        browser_settings = load_browser_fingerprint_settings()
        init_script = self._browser_init_script(browser_settings)
        context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=settings.headless,
            user_agent=browser_settings.user_agent,
            extra_http_headers=browser_settings.extra_headers,
            locale=browser_settings.locale,
            timezone_id=browser_settings.timezone,
            service_workers="block",
            args=[
                "--window-size=1920,1080",
                "--disable-blink-features=AutomationControlled",
                "--disable-component-update",
                "--disable-sync",
                "--no-default-browser-check",
                "--disable-features=ChromeWhatsNewUI",
                f"--user-agent={browser_settings.user_agent}",
                "--start-maximized",
                "--window-position=0,0",
            ],
        )
        # Intercept browser HTTP(S) requests, including redirects and subresources.
        # Blocking service workers prevents them from bypassing context routing.
        await context.route("**/*", self._guard_browser_request)
        page = context.pages[0] if context.pages else await context.new_page()
        if init_script:
            await context.add_init_script(init_script)
        await self._fit_browser_window(page)
        handle = ProviderHandle(
            config=config,
            context=context,
            page=page,
            lock=asyncio.Lock(),
        )
        self._handles[provider_name] = handle
        return handle

    def _browser_init_script(self, browser_settings: BrowserFingerprintSettings) -> str:
        languages = self._browser_languages(browser_settings)
        scripts: list[str] = [
            STEALTH_INIT_SCRIPT.replace("__BROWSER_LANGUAGES__", json.dumps(languages))
        ]
        if browser_settings.platform:
            scripts.append(
                "Object.defineProperty(navigator, 'platform', "
                f"{{ get: () => {json.dumps(browser_settings.platform)} }});"
            )
        return "\n".join(scripts)

    def _browser_languages(self, browser_settings: BrowserFingerprintSettings) -> list[str]:
        accept_language = browser_settings.extra_headers.get("Accept-Language", "")
        languages = [
            part.split(";", 1)[0].strip()
            for part in accept_language.split(",")
            if part.split(";", 1)[0].strip()
        ]
        if browser_settings.locale and browser_settings.locale not in languages:
            languages.insert(0, browser_settings.locale)
        return languages or [browser_settings.locale or "en-US", "en"]

    async def _fit_browser_window(self, page: Page) -> None:
        await page.set_viewport_size(BROWSER_VIEWPORT)
        await page.evaluate("window.moveTo(0,0); window.resizeTo(1920, 1080)")
        if not settings.headless:
            await self._maximize_window()

    async def _maximize_window(self) -> bool:
        if shutil.which("xdotool") is None:
            return False
        command = """
            set -eu
            windows="$(xdotool search --sync --onlyvisible --class chromium)"
            for wid in $windows; do
                xdotool windowmove --sync "$wid" 0 0
                xdotool windowsize --sync "$wid" 1920 1080
            done
            for wid in $windows; do
                geometry="$(xdotool getwindowgeometry "$wid")"
                printf '%s\n' "$geometry" | grep -q "Position: 0,0"
                printf '%s\n' "$geometry" | grep -q "Geometry: 1920x1080"
            done
        """
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return False
            return process.returncode == 0
        except Exception:  # noqa: BLE001 - viewport resizing below remains the fallback.
            return False

    async def _recreate_handle(self, provider_name: str, handle: ProviderHandle) -> ProviderHandle:
        async with self._engine_lock:
            current = self._handles.get(provider_name)
            if current is not None and current is not handle and self._is_handle_alive(current):
                return current
            if current is not None:
                await self._discard_handle(provider_name, current)
            await self.start()
            return await self._create_handle(provider_name)

    def _is_handle_alive(self, handle: ProviderHandle) -> bool:
        try:
            if handle.page.is_closed():
                return False
            return handle.page in handle.context.pages
        except Error as exc:
            if self._is_closed_error(exc):
                return False
            raise

    async def _discard_handle(self, provider_name: str, handle: ProviderHandle) -> None:
        self._handles.pop(provider_name, None)
        await self._close_context(handle.context)

    async def _close_context(self, context: BrowserContext) -> None:
        try:
            await context.close()
        except Error as exc:
            if not self._is_closed_error(exc):
                raise

    async def _guard_browser_request(self, route: Route, request: Request) -> None:
        try:
            await validate_public_http_url_async(request.url)
        except ValueError:
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    async def _goto_provider(self, handle: ProviderHandle) -> ProviderHandle:
        try:
            await handle.page.goto(str(handle.config.url), wait_until="domcontentloaded")
            await handle.page.bring_to_front()
            return handle
        except Error as exc:
            if not self._is_closed_error(exc):
                raise
            handle = await self._recreate_handle(handle.config.name, handle)
            await handle.page.goto(str(handle.config.url), wait_until="domcontentloaded")
            await handle.page.bring_to_front()
            return handle

    @staticmethod
    def _is_closed_error(exc: Error) -> bool:
        return CLOSED_BROWSER_ERROR in str(exc)

    async def _dom_stream(self, handle: ProviderHandle, prompt: str) -> AsyncIterator[str]:
        page = handle.page
        if page.is_closed():
            handle = await self._recreate_handle(handle.config.name, handle)
            page = handle.page

        if not page.url or page.url == "about:blank":
            handle = await self._goto_provider(handle)
            page = handle.page

        previous_count = await self._response_count(page, handle.config)
        await self._fill_prompt(page, handle.config, prompt)
        await self._send_prompt(page, handle.config)

        response_locator = page.locator(handle.config.response_selector)
        started_at = time.monotonic()
        last_text = ""
        last_change = time.monotonic()

        while True:
            await page.wait_for_timeout(250)
            current_count = await response_locator.count()
            if current_count <= previous_count:
                if time.monotonic() - started_at > settings.response_timeout_seconds:
                    raise TimeoutError("Timed out waiting for provider response to start")
                continue

            text = await self._latest_response_text(page, handle.config)
            if text.startswith(last_text):
                delta = text[len(last_text) :]
            else:
                delta = text

            if delta:
                yield delta
                last_text = text
                last_change = time.monotonic()

            idle_for = time.monotonic() - last_change
            timed_out = time.monotonic() - started_at > settings.response_timeout_seconds
            if last_text and idle_for >= settings.response_idle_seconds:
                break
            if timed_out:
                raise TimeoutError("Timed out waiting for provider response to finish")

    async def _network_then_dom_stream(
        self, handle: ProviderHandle, prompt: str
    ) -> AsyncIterator[str]:
        # Provider network protocols change frequently. This hook keeps the public
        # mode available while DOM streaming remains the dependable fallback.
        async for chunk in self._dom_stream(handle, prompt):
            yield chunk

    async def _fill_prompt(self, page: Page, config: ProviderConfig, prompt: str) -> None:
        locator = page.locator(config.input_selector).last
        await locator.wait_for(state="visible", timeout=30_000)
        try:
            await locator.fill(prompt)
        except Error:
            await locator.click()
            await page.keyboard.press("Control+A")
            await page.keyboard.type(prompt)

    async def _send_prompt(self, page: Page, config: ProviderConfig) -> None:
        if config.send_button_selector:
            button = page.locator(config.send_button_selector).last
            try:
                await button.click(timeout=5_000)
                return
            except Error:
                pass
        await page.keyboard.press("Enter")

    async def _response_count(self, page: Page, config: ProviderConfig) -> int:
        try:
            return await page.locator(config.response_selector).count()
        except Error:
            return 0

    async def _latest_response_text(self, page: Page, config: ProviderConfig) -> str:
        selector = config.response_text_selector or config.response_selector
        locator = page.locator(selector).last
        try:
            return (await locator.inner_text(timeout=1_000)).strip()
        except Error:
            return ""

    async def _save_session(self, handle: ProviderHandle) -> None:
        session_path = self._session_path(handle.config)
        await handle.context.storage_state(path=str(session_path))

    def _runtime(
        self, config: ProviderConfig, handle: ProviderHandle | None
    ) -> ProviderRuntime:
        status = handle.status if handle else ProviderStatus.disconnected
        error = handle.error if handle else None
        return ProviderRuntime(
            name=config.name,
            display_name=config.display_name,
            url=str(config.url),
            enabled=config.enabled,
            mode=config.mode,
            status=status,
            error=error,
        )

    def _session_path(self, config: ProviderConfig) -> Path:
        return SESSIONS_DIR / f"{config.name}.json"

    def _user_data_dir(self, config: ProviderConfig) -> Path:
        configured = Path(config.user_data_dir) if config.user_data_dir else None
        return configured or BROWSER_DIR / config.name
