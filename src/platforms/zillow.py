"""Zillow Rental Manager — landlord-side messaging.

Status: SKELETON. enabled = False until selectors are written against
real DOM. Bring online via:
  1. python -m src.main login --platform zillow
  2. python -m src.main diag --platform zillow
  3. Update selectors here, set enabled = True

URL pattern observed in user-supplied example:
  https://www.zillow.com/rental-manager/inbox/<inbox_id>/<thread_id>
The inbox itself is /rental-manager/inbox/.
"""
from __future__ import annotations

from playwright.sync_api import BrowserContext, Page

from .base import InboundMessage

INBOX_URL = "https://www.zillow.com/rental-manager/inbox/"
LOGIN_URL = "https://www.zillow.com/"  # click "Sign In" in the top nav


class ZillowPlatform:
    name = "zillow"
    inbox_url = INBOX_URL
    login_url = LOGIN_URL
    enabled = False  # ← flip to True once selectors below are real

    def poll_inbox(self, ctx: BrowserContext) -> tuple[list[InboundMessage], int]:
        raise NotImplementedError(
            "Zillow.poll_inbox not yet implemented — run "
            "`diag --platform zillow` first"
        )

    def open_thread(self, page: Page, thread_id: str) -> None:
        raise NotImplementedError("Zillow.open_thread not yet implemented")

    def send_reply(self, page: Page, body: str, typing_delay_ms: list[int]) -> None:
        raise NotImplementedError("Zillow.send_reply not yet implemented")
