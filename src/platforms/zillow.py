"""Zillow Rental Manager — landlord-side messaging.

Zillow has the cleanest DOM of the three platforms — semantic data-testid
attributes throughout. Selectors confirmed against the live inbox 2026-05-02:

  conversation-item        thread row in the left rail
  unread-badge             inner element present iff thread has unread inbound
  interactive-chat-bubble  message bubble in the conversation pane
  textarea-autosize        reply input
  participant-name         counterparty name in the rail row

Thread URLs follow:  /rental-manager/inbox/<inbox_id>/<thread_id>
The inbox_id is per-account; we discover it once per poll and cache for
the open_thread call.
"""
from __future__ import annotations

import random
import re
import time

from playwright.sync_api import BrowserContext, Page, TimeoutError as PWTimeout

from .base import InboundMessage

INBOX_URL = "https://www.zillow.com/rental-manager/inbox/"
LOGIN_URL = "https://www.zillow.com/"

_THREAD_PATH_RE = re.compile(r"/rental-manager/inbox/([^/]+)/(\d+)")


def _parse_thread_id(url: str) -> tuple[str | None, str | None]:
    """Pull (inbox_id, thread_id) out of a Zillow inbox URL."""
    m = _THREAD_PATH_RE.search(url)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def _row_meta(row) -> tuple[str | None, str | None, str | None]:
    """Extract (counterparty, listing_title, snippet) from a conversation-item row.

    Row text format (verified): "<Name>\\n<Date>\\n<Listing>\\n<Snippet>\\n<Status>".
    """
    try:
        # participant-name is the cleanest source for the counterparty
        name_el = row.locator('[data-testid="participant-name"]')
        name = (name_el.first.inner_text().strip() if name_el.count() > 0 else "") or None

        text = (row.inner_text() or "").strip()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not name and lines:
            name = lines[0]

        listing = None
        for ln in lines:
            if "5th" in ln.lower() or "street" in ln.lower() or "st " in ln.lower() or "#" in ln:
                if "you:" not in ln.lower() and "interested" not in ln.lower() and len(ln) < 60:
                    listing = ln
                    break
        snippet = lines[-1] if lines else None
        return name, listing, snippet
    except Exception:
        return None, None, None


class ZillowPlatform:
    name = "zillow"
    inbox_url = INBOX_URL
    login_url = LOGIN_URL
    enabled = True

    # Cached after the first successful poll; reused by open_thread.
    _inbox_id: str | None = None

    def poll_inbox(self, ctx: BrowserContext) -> tuple[list[InboundMessage], int]:
        page = ctx.new_page()
        page.set_default_navigation_timeout(60_000)
        page.set_default_timeout(45_000)
        try:
            page.goto(INBOX_URL, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except PWTimeout:
                pass
            page.wait_for_timeout(6000)

            if "login" in page.url.lower() or "zsignin" in page.url.lower():
                raise RuntimeError(
                    "Not logged in to Zillow. Run "
                    "`python -m src.main login --platform zillow` first."
                )

            # Cache inbox_id for open_thread later.
            inbox_id, _ = _parse_thread_id(page.url)
            if inbox_id:
                self._inbox_id = inbox_id

            print(f"  inbox url: {page.url}")
            try:
                page.locator('[data-testid="conversation-item"]').first.wait_for(timeout=15_000)
            except Exception:
                print("  no conversation-item found — inbox may be empty or DOM changed")
                return [], 0

            rows = page.locator('[data-testid="conversation-item"]').all()
            print(f"  total threads in rail: {len(rows)}")

            results: list[InboundMessage] = []
            unread_count = 0

            for row in rows:
                # Skip threads with no unread badge
                if row.locator('[data-testid="unread-badge"]').count() == 0:
                    continue
                unread_count += 1

                name, listing, _snippet = _row_meta(row)
                if not name:
                    continue

                # Capture URL before click so we can detect navigation completion
                pre_url = page.url
                try:
                    row.click()
                    # Wait for the URL to actually change (history-API navigation)
                    deadline = time.time() + 8
                    while time.time() < deadline and page.url == pre_url:
                        page.wait_for_timeout(200)
                    page.wait_for_timeout(2500)  # let bubbles render
                    # Wait for at least one bubble in the conversation pane
                    page.locator('[data-testid="interactive-chat-bubble"]').first.wait_for(timeout=8000)
                except Exception as e:
                    print(f"  click/load error on {name!r}: {e}")
                    continue

                _, thread_id = _parse_thread_id(page.url)
                if not thread_id:
                    print(f"  could not parse thread_id from URL: {page.url}")
                    continue

                # Read the latest bubble (oldest → newest in document order).
                # Strip the leading sender-name line that Zillow includes inside
                # each bubble (e.g. "Victor Adame\n\nI am interested in...").
                try:
                    bubbles = page.locator('[data-testid="interactive-chat-bubble"]').all()
                    body = None
                    for b in reversed(bubbles):
                        t = (b.inner_text() or "").strip()
                        if not t or len(t) >= 4000:
                            continue
                        # Drop the first line if it equals the counterparty name
                        # (Zillow prepends sender name inside each bubble).
                        lines = t.splitlines()
                        if lines and name and lines[0].strip().lower() == name.lower():
                            t = "\n".join(lines[1:]).strip()
                        if t:
                            body = t
                            break
                except Exception as e:
                    print(f"  read error on {name!r}: {e}")
                    continue

                if not body:
                    print(f"  no body extracted for {name!r}")
                    continue

                msg_id = f"zillow::{thread_id}::{hash(body) & 0xFFFFFFFF:x}"
                results.append(
                    InboundMessage(
                        platform=self.name,
                        thread_id=thread_id,
                        msg_id=msg_id,
                        counterparty=name,
                        listing_title=listing,
                        body=body,
                    )
                )

            print(f"  {unread_count} unread thread(s); {len(results)} with readable body")
            return results, len(rows)
        finally:
            page.close()

    def open_thread(self, page: Page, thread_id: str) -> None:
        # We need inbox_id. If not cached, visit the inbox to discover it.
        if not self._inbox_id:
            page.goto(INBOX_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(5000)
            inbox_id, _ = _parse_thread_id(page.url)
            self._inbox_id = inbox_id

        if not self._inbox_id:
            raise RuntimeError("Zillow inbox_id not discoverable; cannot open thread")

        url = f"https://www.zillow.com/rental-manager/inbox/{self._inbox_id}/{thread_id}"
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(4000)

    def send_reply(self, page: Page, body: str, typing_delay_ms: list[int]) -> None:
        box = page.locator('[data-testid="textarea-autosize"]').first
        box.click()
        box.fill("")
        for ch in body:
            box.type(ch, delay=random.randint(*typing_delay_ms))
        time.sleep(random.uniform(0.5, 1.5))
        # Send button — fall back to Cmd/Ctrl+Enter or Enter
        send_btn = page.get_by_role("button", name=re.compile(r"^send$", re.I))
        if send_btn.count() > 0:
            send_btn.first.click()
        else:
            page.keyboard.press("Enter")
        time.sleep(random.uniform(1.0, 2.0))
