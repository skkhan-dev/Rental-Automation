"""Send a reply into the currently open thread."""
from __future__ import annotations

import random
import re
import time
from playwright.sync_api import Page


def send_reply(page: Page, body: str, typing_delay_ms: list[int]) -> None:
    # Prefer the "Write to ..." textbox (the reply input), falling back to last textbox
    candidate = page.get_by_role("textbox", name=re.compile(r"^Write to"))
    box = candidate.first if candidate.count() > 0 else page.get_by_role("textbox").last
    box.click()
    box.fill("")
    for ch in body:
        box.type(ch, delay=random.randint(*typing_delay_ms))
    time.sleep(random.uniform(0.3, 1.0))
    page.keyboard.press("Enter")
    time.sleep(random.uniform(0.5, 1.5))


def open_thread(page, thread_id: str) -> None:
    page.goto(
        f"https://www.messenger.com/marketplace/t/{thread_id}/",
        wait_until="domcontentloaded",
        timeout=60_000,
    )
    page.wait_for_timeout(7000)
