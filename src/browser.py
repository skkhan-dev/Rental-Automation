"""Persistent Playwright context. First run requires you to log into each
platform manually in the launched window; cookies persist in
data/browser_profile.

We use `channel="chrome"` — the real Google Chrome binary, not Playwright's
bundled Chromium for Testing. That gives us:
  - A matching TLS fingerprint (JA3) that Cloudflare/PerimeterX expect
  - Real Widevine, codec support, and Google components
  - The same User-Agent string a normal user would send (so we DON'T
    override user_agent — Chrome handles it)

If Chrome isn't installed, launch raises and the cycle records a failure.
On macOS the install path is /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
"""
from __future__ import annotations

from contextlib import contextmanager

from playwright.sync_api import BrowserContext, sync_playwright

from .config import PROFILE_DIR

# Minimal stealth: patch the most obvious headless/automation tells before
# any page script runs. This isn't a full bypass — Cloudflare etc. will still
# fingerprint heavily — but it gets past the basic "navigator.webdriver" gate
# that triggers Cloudflare's "verify you are human" checkbox to reject clicks.
_STEALTH_INIT = """
// Hide webdriver flag
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

// Realistic plugins length
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});

// Realistic languages
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});

// Patch Chrome runtime
window.chrome = window.chrome || { runtime: {} };

// Patch permissions query for "notifications"
const _origQuery = window.navigator.permissions && window.navigator.permissions.query;
if (_origQuery) {
  window.navigator.permissions.query = (p) =>
    p && p.name === 'notifications'
      ? Promise.resolve({state: Notification.permission})
      : _origQuery(p);
}
"""

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
]


@contextmanager
def context(headless: bool = False):
    with sync_playwright() as p:
        ctx: BrowserContext = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=headless,
            channel="chrome",                    # ← real Chrome, not Chromium-for-Testing
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            args=LAUNCH_ARGS,
        )
        ctx.add_init_script(_STEALTH_INIT)
        try:
            yield ctx
        finally:
            ctx.close()
