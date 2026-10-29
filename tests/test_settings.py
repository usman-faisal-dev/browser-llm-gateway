from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.settings import (
    COMMON_TIMEZONES,
    DEFAULT_USER_AGENT,
    USER_AGENT_PLATFORM_MAP,
    USER_AGENT_PRESETS,
    BrowserFingerprintSettings,
)


class BrowserFingerprintSettingsTests(unittest.TestCase):
    def test_accepts_browser_fingerprint_dropdown_values(self) -> None:
        settings = BrowserFingerprintSettings(
            user_agent=USER_AGENT_PRESETS["Safari 17 macOS"],
            timezone="Asia/Tokyo",
        )

        self.assertEqual(settings.user_agent, USER_AGENT_PRESETS["Safari 17 macOS"])
        self.assertEqual(settings.timezone, "Asia/Tokyo")
        self.assertIn("Europe/London", COMMON_TIMEZONES)
        self.assertGreaterEqual(len(COMMON_TIMEZONES), 20)
        self.assertEqual(USER_AGENT_PRESETS["Custom"], DEFAULT_USER_AGENT)
        self.assertEqual(USER_AGENT_PRESETS["Chrome 124 Windows"], DEFAULT_USER_AGENT)
        self.assertEqual(len(USER_AGENT_PRESETS), 11)
        self.assertEqual(USER_AGENT_PLATFORM_MAP["Chrome 124 Windows"], "Win32")
        self.assertEqual(USER_AGENT_PLATFORM_MAP["Chrome 120 macOS"], "MacIntel")
        self.assertEqual(USER_AGENT_PLATFORM_MAP["Chrome Mobile Android"], "Linux armv8l")
        self.assertIsNone(USER_AGENT_PLATFORM_MAP["Custom"])

    def test_accepts_custom_browser_fingerprint_user_agent(self) -> None:
        settings = BrowserFingerprintSettings(user_agent="Custom Agent")

        self.assertEqual(settings.user_agent, "Custom Agent")

    def test_rejects_non_dropdown_browser_fingerprint_timezone(self) -> None:
        with self.assertRaises(ValidationError):
            BrowserFingerprintSettings(timezone="Mars/Olympus_Mons")


if __name__ == "__main__":
    unittest.main()
