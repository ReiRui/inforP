from __future__ import annotations

from pathlib import Path


class BrowserProfileConnector:
    """User-authorized browser profile connector.

    This connector intentionally pauses for user-driven login. It does not bypass
    captcha, SMS, QR login, paywalls, or platform access controls.
    """

    def __init__(self, profiles_dir: Path = Path("profiles")):
        self.profiles_dir = profiles_dir

    def profile_path(self, platform: str) -> Path:
        return self.profiles_dir / platform

    def bootstrap_login(self, platform: str, login_url: str) -> None:
        from playwright.sync_api import sync_playwright

        profile = self.profile_path(platform)
        profile.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=False,
            )
            page = browser.new_page()
            page.goto(login_url)
            input("Complete login in the browser, then press Enter here to save the profile...")
            browser.close()

