"""Platform interface. Each rental marketplace (FB, Zillow, Avail, …)
implements this protocol and registers itself in __init__.REGISTRY."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from playwright.sync_api import BrowserContext, Page


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
