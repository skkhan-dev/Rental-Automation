"""Avail.com landlord-side messaging.

Architecture:
  - Avail's messaging is a drawer panel (role=dialog), not a separate inbox URL.
  - The drawer is shared across all units owned by the landlord.
  - Threads have no stable URL — we identify them by counterparty name (which
    is unique within the inbox in practice).
  - Inbound messages are typically auto-generated form inquiries:
    "Hello, I'd like more information about ..."

Tested 2026-05-02 against the real DOM (see src/diag.py output).
"""
from __future__ import annotations

import random
import re
import time

from playwright.sync_api import BrowserContext, Page, TimeoutError as PWTimeout

from .base import InboundMessage

# Any landlord-side URL that has the messages drawer button works as a starting
# point. Pick a unit page (the drawer button shows on every unit screen).
INBOX_URL = "https://www.avail.com/app/landlords/units/61431973/listings"
LOGIN_URL = "https://www.avail.com/"

_DRAWER_BTN_NAME = re.compile(r"messages drawer", re.IGNORECASE)
_UNREAD_BTN_NAME = re.compile(r"^unread\b", re.IGNORECASE)
_REPLY_BOX_NAME = re.compile(r"^Type your message", re.IGNORECASE)


def _open_drawer(page: Page) -> None:
    """Click the 'Open messages drawer' button and wait for the dialog."""
    btn = page.get_by_role("button", name=_DRAWER_BTN_NAME)
    btn.first.click()
    page.get_by_role("dialog").wait_for(timeout=10_000)
    page.wait_for_timeout(1500)


def _filter_unread(page: Page) -> None:
    """Inside the dialog, click the 'Unread' filter pill."""
    dialog = page.get_by_role("dialog")
    pill = dialog.get_by_role("button", name=_UNREAD_BTN_NAME)
    if pill.count() > 0:
        pill.first.click()
        page.wait_for_timeout(1500)


def _thread_rail_items(page: Page):
    """Locator for the thread rows in the drawer's left rail.

    Heuristic: inside the dialog, find list/listitem-like elements that
    contain an applicant name + listing snippet but not the conversation
    pane on the right. We use a CSS selector that targets buttons or links
    with text indicating a thread row.
    """
    dialog = page.get_by_role("dialog")
    # Each thread row is clickable; in Avail's DOM these surface as buttons
    # whose name starts with the applicant's name. We can't filter by name
    # before knowing it, so dump all dialog buttons and filter by structure.
    return dialog.locator(
        "button:has(div), [role='button']:has(div), [role='listitem']"
    )


_TIMESTAMP_LINE_RE = re.compile(r"^\d{1,2}:\d{2}\s*[AP]M$", re.IGNORECASE)


def _strip_trailing_timestamp(text: str) -> str:
    """Remove the trailing 'HH:MM AM/PM' line from a message bubble's text."""
    lines = text.rstrip().split("\n")
    while lines and (
        not lines[-1].strip()
        or _TIMESTAMP_LINE_RE.match(lines[-1].strip())
    ):
        lines.pop()
    return "\n".join(lines).strip()


def _read_open_thread(page: Page) -> str | None:
    """Return the latest inbound message body from the conversation pane.

    Avail renders each message bubble as a div containing
    "<body>\n\n<HH:MM AM/PM>". The right pane lists bubbles oldest → newest;
    the most recent inbound is therefore the LAST bubble in document order.

    We deduplicate by text and exclude:
      - The reply textbox / search box
      - Rail rows (they contain 'Applicant |')
      - Pure date headers ('Thu Apr 2, 2026')
    """
    page.wait_for_timeout(1500)
    dialog = page.get_by_role("dialog")

    BAD_FRAGMENTS = ("Applicant |", "Applicant\xa0|", "Type your message",
                     "Search conversations", "Select a conversation")
    DATE_HEADER_RE = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\w+\s+\d+", re.I)

    candidates: list[str] = []
    seen: set[str] = set()
    for div in dialog.locator("div").all():
        try:
            t = (div.inner_text() or "").strip()
        except Exception:
            continue
        if not (40 < len(t) < 4000):
            continue
        if any(b in t for b in BAD_FRAGMENTS):
            continue
        if DATE_HEADER_RE.match(t.split("\n", 1)[0].strip()):
            continue
        if t in seen:
            continue
        seen.add(t)
        # Strip a trailing timestamp line if present
        cleaned = _strip_trailing_timestamp(t)
        if 20 < len(cleaned) < 4000:
            candidates.append(cleaned)

    if not candidates:
        return None
    # Last in document order = most recent message
    return candidates[-1]


class AvailPlatform:
    name = "avail"
    inbox_url = INBOX_URL
    login_url = LOGIN_URL
    enabled = True

    def poll_inbox(self, ctx: BrowserContext) -> tuple[list[InboundMessage], int]:
        page = ctx.new_page()
        page.set_default_navigation_timeout(60_000)
        page.set_default_timeout(30_000)
        try:
            page.goto(INBOX_URL, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except PWTimeout:
                pass
            page.wait_for_timeout(5000)

            if "sign_in" in page.url or "login" in page.url.lower():
                raise RuntimeError(
                    "Not logged in to Avail. Run "
                    "`python -m src.main login --platform avail` first."
                )

            print(f"  inbox url: {page.url}")
            try:
                _open_drawer(page)
            except Exception as e:
                print(f"  could not open drawer: {e}")
                return [], 0

            _filter_unread(page)

            # Now scrape the unread threads from the rail
            results: list[InboundMessage] = []
            seen_names: set[str] = set()
            total_seen = 0

            # The rail items are clickable rows whose first line is the
            # counterparty name. Scope to the dialog and pull each row's text.
            dialog = page.get_by_role("dialog")
            rows = dialog.locator("li, [role='listitem'], button:has(div):has(span)").all()
            print(f"  drawer rail candidates: {len(rows)}")

            for row in rows:
                try:
                    text = (row.inner_text() or "").strip()
                except Exception:
                    continue
                if not text or len(text) > 300:
                    continue
                # Row text looks like:
                #   "<INITIALS>\n<Full Name>\n\nApplicant | <listing>\n\n<snippet>\n\n<time>"
                # Avatar initials come first; the actual name is the next line.
                lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                if len(lines) < 3:
                    continue
                # Avatar initials are typically 1-3 uppercase letters
                if re.fullmatch(r"[A-Z]{1,3}", lines[0]):
                    name = lines[1]
                else:
                    name = lines[0]
                if name in seen_names or len(name) > 60 or "@" in name:
                    continue
                seen_names.add(name)
                total_seen += 1

                # Listing reference: "Applicant | <listing>"
                listing = None
                for ln in lines[1:4]:
                    if "applicant" in ln.lower() and "|" in ln:
                        listing = ln.split("|", 1)[1].strip()
                        break

                # Open this thread by clicking the row, then read the message
                try:
                    row.click()
                    page.wait_for_timeout(2500)
                    body = _read_open_thread(page)
                except Exception as e:
                    print(f"  read error on {name!r}: {e}")
                    continue
                if not body:
                    continue

                # Synthetic stable thread ID (Avail has no URL per thread)
                thread_id = f"avail::{name}".replace(" ", "_").lower()
                msg_id = f"{thread_id}::{hash(body) & 0xFFFFFFFF:x}"

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

            print(f"  scanned {total_seen} unread thread rows; {len(results)} with readable body")
            return results, total_seen
        finally:
            page.close()

    def open_thread(self, page: Page, thread_id: str) -> None:
        """Navigate to inbox, open drawer, search by counterparty name."""
        # thread_id format: "avail::first_name_last_name"
        name_slug = thread_id.split("::", 1)[-1]
        name_query = name_slug.replace("_", " ").strip()

        page.goto(INBOX_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(5000)
        _open_drawer(page)

        dialog = page.get_by_role("dialog")
        search = dialog.get_by_role("textbox", name=re.compile(r"Search conversations", re.I))
        search.first.fill(name_query)
        page.wait_for_timeout(1500)

        # First matching row in the filtered rail
        rows = dialog.locator("li, [role='listitem'], button:has(div):has(span)").all()
        for row in rows:
            try:
                text = (row.inner_text() or "").strip()
                if name_query.lower() in text.lower():
                    row.click()
                    page.wait_for_timeout(2500)
                    return
            except Exception:
                continue
        raise RuntimeError(f"Could not find Avail thread for {name_query!r}")

    def send_reply(self, page: Page, body: str, typing_delay_ms: list[int]) -> None:
        dialog = page.get_by_role("dialog")
        box = dialog.get_by_role("textbox", name=_REPLY_BOX_NAME).first
        box.click()
        box.fill("")
        for ch in body:
            box.type(ch, delay=random.randint(*typing_delay_ms))
        time.sleep(random.uniform(0.5, 1.5))
        # Try clicking a Send button first; fall back to Enter
        send_btn = dialog.get_by_role("button", name=re.compile(r"^send$", re.I))
        if send_btn.count() > 0:
            send_btn.first.click()
        else:
            page.keyboard.press("Enter")
        time.sleep(random.uniform(0.5, 1.5))
