"""Facebook Marketplace messages — scraped via messenger.com.

Selectors confirmed against messenger.com's marketplace inbox: thread anchors
have hrefs like /marketplace/t/<id>/, aria-label "Group chat: <Name> · <Listing>",
and unread state is indicated by the substring "Unread message:" in the link
text. Messages render as <div dir="auto"> in the thread pane. The reply
textbox has aria-label starting with "Write to ...".
"""
from __future__ import annotations

import random
import re
import time

from playwright.sync_api import BrowserContext, Page, TimeoutError as PWTimeout

from .base import InboundMessage

INBOX_URL = "https://www.messenger.com/marketplace"
THREAD_URL = "https://www.messenger.com/marketplace/t/{tid}/"

_THREAD_ID_RE = re.compile(r"/(?:messages|marketplace)/t/(\d+)")
_ARIA_RE = re.compile(r"^Group chat:\s*(.+?)\s*·\s*(.+)$")


def _scan_inbox(page: Page) -> list[dict]:
    """Return list of {thread_id, counterparty, listing, unread} for visible threads."""
    out = []
    seen = set()
    for a in page.locator('a[href*="/marketplace/t/"], a[href*="/messages/t/"]').all():
        try:
            href = a.get_attribute("href") or ""
            m = _THREAD_ID_RE.search(href)
            if not m:
                continue
            tid = m.group(1)
            if tid in seen or tid == "marketplace":
                continue
            seen.add(tid)

            text = a.inner_text() or ""
            aria = a.get_attribute("aria-label") or ""
            unread = "Unread message:" in text

            counterparty, listing = "Unknown", None
            am = _ARIA_RE.match(aria)
            if am:
                counterparty = am.group(1).strip()
                listing = am.group(2).strip()

            out.append({
                "thread_id": tid,
                "counterparty": counterparty,
                "listing": listing,
                "unread": unread,
            })
        except Exception:
            continue
    return out


def _read_latest_inbound(page: Page, tid: str) -> str | None:
    """Navigate to thread, return the latest inbound message body (or None)."""
    page.goto(THREAD_URL.format(tid=tid), wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(7000)  # FB SPA hydration

    msgs = []
    for d in page.locator('div[dir="auto"]').all():
        try:
            text = (d.inner_text() or "").strip()
            if text and len(text) < 4000:
                msgs.append(text)
        except Exception:
            continue

    if not msgs:
        return None

    BAD_PREFIXES = ("Write to ", "Press Enter", "Aa")
    for m in reversed(msgs):
        if not m.startswith(BAD_PREFIXES):
            return m
    return None


class FacebookPlatform:
    """Facebook Marketplace platform implementation."""

    name = "facebook"
    inbox_url = INBOX_URL
    login_url = "https://www.facebook.com/login"
    enabled = True

    def poll_inbox(self, ctx: BrowserContext) -> tuple[list[InboundMessage], int]:
        page = ctx.new_page()
        page.set_default_navigation_timeout(60_000)
        page.set_default_timeout(60_000)
        try:
            page.goto(INBOX_URL, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except PWTimeout:
                pass
            page.wait_for_timeout(8000)
            try:
                page.locator(
                    'a[href*="/marketplace/t/"], a[href*="/messages/t/"]'
                ).first.wait_for(timeout=15_000)
            except Exception:
                pass

            if "login" in page.url.lower():
                raise RuntimeError(
                    "Not logged in to Facebook. Run `python -m src.main login` first."
                )

            print(f"  inbox url: {page.url}")
            threads = _scan_inbox(page)
            unread = [t for t in threads if t["unread"]]
            print(f"  scanned {len(threads)} threads; {len(unread)} unread")
            for t in threads[:5]:
                print(
                    f"    - tid={t['thread_id'][:14]} unread={t['unread']} "
                    f"who={t['counterparty']!r} listing={t['listing']!r}"
                )

            results: list[InboundMessage] = []
            for t in unread:
                try:
                    body = _read_latest_inbound(page, t["thread_id"])
                except Exception as e:
                    print(f"  read error on {t['thread_id']}: {e}")
                    continue
                if not body:
                    continue
                msg_id = f"{t['thread_id']}::{hash(body) & 0xFFFFFFFF:x}"
                results.append(
                    InboundMessage(
                        platform=self.name,
                        thread_id=t["thread_id"],
                        msg_id=msg_id,
                        counterparty=t["counterparty"],
                        listing_title=t["listing"],
                        body=body,
                    )
                )
            return results, len(threads)
        finally:
            page.close()

    def open_thread(self, page: Page, thread_id: str) -> None:
        page.goto(
            THREAD_URL.format(tid=thread_id),
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        page.wait_for_timeout(7000)

    def send_reply(self, page: Page, body: str, typing_delay_ms: list[int]) -> None:
        candidate = page.get_by_role("textbox", name=re.compile(r"^Write to"))
        box = (
            candidate.first
            if candidate.count() > 0
            else page.get_by_role("textbox").last
        )
        box.click()
        box.fill("")
        for ch in body:
            box.type(ch, delay=random.randint(*typing_delay_ms))
        time.sleep(random.uniform(0.3, 1.0))
        page.keyboard.press("Enter")
        time.sleep(random.uniform(0.5, 1.5))
