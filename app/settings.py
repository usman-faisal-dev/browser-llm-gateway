from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
PROVIDERS_DIR = DATA_DIR / "providers"
SESSIONS_DIR = DATA_DIR / "sessions"
BROWSER_DIR = DATA_DIR / "browser"
BROWSER_SETTINGS_PATH = DATA_DIR / "browser_settings.json"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

USER_AGENT_PRESETS: dict[str, str] = {
    "Custom": DEFAULT_USER_AGENT,
    "Chrome 124 Windows": DEFAULT_USER_AGENT,
    "Chrome 120 macOS": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Firefox 123 Windows": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) "
        "Gecko/20100101 Firefox/123.0"
    ),
    "Edge 124 Windows": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
    ),
    "Safari 17 macOS": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
    ),
    "Chrome Mobile Android": (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.6367.83 Mobile Safari/537.36"
    ),
    "Firefox Mobile Android": (
        "Mozilla/5.0 (Android 14; Mobile; rv:123.0) Gecko/123.0 Firefox/123.0"
    ),
    "Chrome 124 Linux": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Opera Windows": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 OPR/109.0.0.0"
    ),
    "Brave Windows": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Brave/124.0"
    ),
}

USER_AGENT_PLATFORM_MAP: dict[str, str | None] = {
    "Custom": None,
    "Chrome 124 Windows": "Win32",
    "Chrome 120 macOS": "MacIntel",
    "Firefox 123 Windows": "Win32",
    "Edge 124 Windows": "Win32",
    "Safari 17 macOS": "MacIntel",
    "Chrome Mobile Android": "Linux armv8l",
    "Firefox Mobile Android": "Linux armv8l",
    "Chrome 124 Linux": "Linux x86_64",
    "Opera Windows": "Win32",
    "Brave Windows": "Win32",
}

COMMON_TIMEZONES: tuple[str, ...] = (
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Phoenix",
    "America/Anchorage",
    "Pacific/Honolulu",
    "America/Toronto",
    "America/Mexico_City",
    "America/Sao_Paulo",
    "Europe/London",
    "Europe/Dublin",
    "Europe/Paris",
    "Europe/Berlin",
    "Europe/Madrid",
    "Europe/Rome",
    "Europe/Amsterdam",
    "Europe/Zurich",
    "Europe/Stockholm",
    "Europe/Warsaw",
    "Europe/Athens",
    "Europe/Istanbul",
    "Africa/Cairo",
    "Africa/Johannesburg",
    "Asia/Dubai",
    "Asia/Jerusalem",
    "Asia/Kolkata",
    "Asia/Bangkok",
    "Asia/Singapore",
    "Asia/Shanghai",
    "Asia/Hong_Kong",
    "Asia/Seoul",
    "Asia/Tokyo",
    "Australia/Perth",
    "Australia/Sydney",
    "Pacific/Auckland",
)


def default_extra_headers() -> dict[str, str]:
    return {
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-CH-UA": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": '"Windows"',
        "Upgrade-Insecure-Requests": "1",
    }


class Settings(BaseSettings):
    host: str = "127.0.0.1"
    port: int = 9920
    default_model: str = "chatgpt"
    headless: bool = False
    response_idle_seconds: float = 1.5
    response_timeout_seconds: float = 180.0
    user_agent: str = DEFAULT_USER_AGENT
    extra_headers: dict[str, str] = Field(default_factory=default_extra_headers)
    disable_webdriver: bool = True
    locale: str = "en-US"
    timezone: str = "America/New_York"
    platform: str = "Win32"

    model_config = SettingsConfigDict(env_prefix="LLM_BRIDGE_", env_file=".env")


settings = Settings()


class BrowserFingerprintSettings(BaseModel):
    user_agent: str = settings.user_agent
    extra_headers: dict[str, str] = Field(default_factory=lambda: dict(settings.extra_headers))
    disable_webdriver: bool = settings.disable_webdriver
    locale: str = settings.locale
    timezone: str = settings.timezone
    platform: str = settings.platform

    model_config = ConfigDict(validate_assignment=True)

    @field_validator("timezone")
    @classmethod
    def timezone_must_be_common(cls, value: str) -> str:
        if value not in COMMON_TIMEZONES:
            raise ValueError("timezone must be one of the browser fingerprint presets")
        return value


def ensure_data_dirs() -> None:
    for directory in (DATA_DIR, PROVIDERS_DIR, SESSIONS_DIR, BROWSER_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def load_browser_fingerprint_settings() -> BrowserFingerprintSettings:
    ensure_data_dirs()
    if not BROWSER_SETTINGS_PATH.exists():
        return BrowserFingerprintSettings()
    try:
        return BrowserFingerprintSettings.model_validate_json(
            BROWSER_SETTINGS_PATH.read_text(encoding="utf-8")
        )
    except Exception:
        return BrowserFingerprintSettings()


def save_browser_fingerprint_settings(
    browser_settings: BrowserFingerprintSettings | dict[str, Any],
) -> BrowserFingerprintSettings:
    ensure_data_dirs()
    validated = BrowserFingerprintSettings.model_validate(browser_settings)
    BROWSER_SETTINGS_PATH.write_text(
        validated.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return validated
