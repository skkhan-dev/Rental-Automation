"""Platform interface. Each rental marketplace (FB, Zillow, Avail, …)
implements this protocol and registers itself in __init__.REGISTRY."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from playwright.sync_api import BrowserContext, Page


class BotChallengeDetected(Exception):
    """Raised when poll_inbox detects an anti-bot challenge (login wall,
    Cloudflare 'verify you are human', FB PIN prompt, etc).

    The cycle catches this specifically and fires a notification rather
    than treating it as a generic failure."""
    pass


# Heuristic patterns for bot challenges across all three platforms.
_CHALLENGE_URL_PATTERNS = (
    "/login", "/sign_in", "/signin", "/checkpoint", "/challenge",
    "/captcha", "/verify", "/zsignin",
)
_CHALLENGE_TITLE_PATTERNS = (
    "verify", "security check", "just a moment", "challenge",
    "captcha", "log in", "sign in", "checkpoint",
)
_CHALLENGE_TEXT_PATTERNS = (
    "verify you are human", "press and hold", "let's confirm",
    "are you a robot", "checking your browser", "we'll text you a code",
    "enter the code we sent", "two-factor", "2-step verification",
    "complete this challenge",
)


def detect_challenge(page: Page) -> str | None:
    """Return a short description of the bot challenge, or None if clear."""
    try:
        url = (page.url or "").lower()
        for pat in _CHALLENGE_URL_PATTERNS:
            if pat in url:
                return f"URL suggests challenge ({url[:80]})"
    except Exception:
        pass
    try:
        title = (page.title() or "").lower()
        for pat in _CHALLENGE_TITLE_PATTERNS:
            if pat in title:
                return f"page title: {title[:80]!r}"
    except Exception:
        pass
    try:
        body = (page.locator("body").inner_text() or "")[:2000].lower()
        for pat in _CHALLENGE_TEXT_PATTERNS:
            if pat in body:
                return f"page text contains {pat!r}"
    except Exception:
        pass
    return None


@dataclass
class InboundMessage:
    """One unread message picked up from a platform's inbox."""
    platform: str
    thread_id: str
    msg_id: str
    counterparty: str
    listing_title: str | None
    body: str


class Platform(Protocol):
    """The minimum surface every platform must implement.

    Lifecycle within one cycle:
      1. cycle.start()
      2. platform.poll_inbox(ctx)  → unread messages + total scanned
      3. for each message: draft via Claude, then platform.open_thread + send_reply
      4. cycle.end()

    Each platform manages its own URL space, DOM selectors, and login flow.
    Authentication state lives in the shared Playwright persistent profile;
    the user logs in once per platform via `python -m src.main login --platform <name>`.
    """

    name: str        # short id: "facebook", "zillow", "avail"
    inbox_url: str   # canonical inbox URL (also used by `inspect` and `diag`)
    login_url: str   # where to send the user for first-time login
    enabled: bool    # if False, platform is registered but not polled

    def poll_inbox(self, ctx: BrowserContext) -> tuple[list[InboundMessage], int]:
        """Return (unread inbound messages, total threads visible on inbox)."""
        ...

    def open_thread(self, page: Page, thread_id: str) -> None:
        """Navigate the given page to the thread and wait for it to render."""
        ...

    def send_reply(self, page: Page, body: str, typing_delay_ms: list[int]) -> None:
        """Type and send a reply into the currently open thread."""
        ...
