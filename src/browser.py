"""Persistent Playwright context. First run requires you to log into FB
manually in the launched window; cookies persist in data/browser_profile."""
from contextlib import contextmanager
from playwright.sync_api import sync_playwright, BrowserContext

from .config import PROFILE_DIR

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


@contextmanager
def context(headless: bool = False):
    with sync_playwright() as p:
        ctx: BrowserContext = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=headless,
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        try:
            yield ctx
        finally:
            ctx.close()
