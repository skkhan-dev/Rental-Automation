"""Avail.com landlord-side messaging.

Status: SKELETON. `enabled = False` until selectors are written against
real DOM (see src/diag.py).

Login URL and inbox URL are best-effort guesses that will be confirmed
when the user runs `python -m src.main login --platform avail`.
"""
from __future__ import annotations

from playwright.sync_api import BrowserContext, Page

from .base import InboundMessage

# Best-effort starting URLs — confirm by manually navigating.
INBOX_URL = "https://www.avail.com/app/landlords/inbox"
LOGIN_URL = "https://www.avail.com/users/sign_in"


class AvailPlatform:
    """Avail platform — UNDER CONSTRUCTION.

    To bring online:
      1. python -m src.main login --platform avail   (manual login, cookies persist)
      2. python -m src.main diag --platform avail    (dump real DOM)
      3. Update _scan_inbox / _read_latest_inbound / send_reply with real selectors
      4. Set enabled = True
    """

    name = "avail"
    inbox_url = INBOX_URL
    login_url = LOGIN_URL
    enabled = False  # ← flip to True once selectors below are real

    def poll_inbox(self, ctx: BrowserContext) -> tuple[list[InboundMessage], int]:
        raise NotImplementedError(
            "Avail.poll_inbox not yet implemented — run `diag --platform avail` first"
        )

    def open_thread(self, page: Page, thread_id: str) -> None:
        raise NotImplementedError("Avail.open_thread not yet implemented")

    def send_reply(self, page: Page, body: str, typing_delay_ms: list[int]) -> None:
        raise NotImplementedError("Avail.send_reply not yet implemented")
