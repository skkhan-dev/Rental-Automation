"""Platform-agnostic DOM diagnostic.

Run via:  python -m src.main diag --platform <name>

Opens the platform's inbox URL, waits for hydration, and dumps:
  - URL + title
  - Anchor href histogram (find the thread URL pattern)
  - Up to 30 anchors with non-empty aria-label or text
  - Top role=row / role=button / role=listitem elements with text
  - Reply textbox candidates (aria-label, placeholder)
  - A screenshot saved to data/<platform>_inbox.png

Use the output to fill in selectors in src/platforms/<name>.py.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from . import browser
from .config import DATA_DIR
from .platforms.base import Platform


def dump_inbox(platform: Platform, wait_seconds: int = 12) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    screenshot_path = DATA_DIR / f"{platform.name}_inbox.png"

    with browser.context(headless=False) as ctx:
        page = ctx.new_page()
        page.set_default_navigation_timeout(60_000)
        page.goto(platform.inbox_url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(wait_seconds * 1000)

        if "login" in page.url.lower() or "sign_in" in page.url.lower():
            print(f"⚠️  Looks like you're not logged in (current URL: {page.url}).")
            print(f"    Run: python -m src.main login --platform {platform.name}")
            return

        print(f"=== {platform.name} inbox diagnostic ===")
        print(f"url:    {page.url}")
        print(f"title:  {page.title()}")

        # Save full-page screenshot for visual reference
        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
            print(f"screenshot saved: {screenshot_path}")
        except Exception as e:
            print(f"screenshot failed: {e}")

        # Histogram of anchor href prefixes (first 30 chars)
        print("\n--- href prefix histogram (top 20) ---")
        prefixes: Counter = Counter()
        for a in page.locator("a").all():
            try:
                href = a.get_attribute("href") or ""
                if href:
                    prefixes[href[:30]] += 1
            except Exception:
                pass
        for pre, n in prefixes.most_common(20):
            print(f"  {n:4d}  {pre}")

        # Anchors with aria-label or text — usually the thread list
        print("\n--- anchors with aria-label or text (first 30) ---")
        shown = 0
        for a in page.locator("a").all():
            try:
                href = (a.get_attribute("href") or "")[:90]
                aria = (a.get_attribute("aria-label") or "")[:80]
                text = (a.inner_text() or "").strip().replace("\n", " | ")[:90]
                if aria or text:
                    print(f"  href={href}  aria={aria!r}  text={text!r}")
                    shown += 1
                    if shown >= 30:
                        break
            except Exception:
                pass

        # Role-based structural probe
        for role in ("row", "listitem", "button"):
            els = page.get_by_role(role).all()
            if not els:
                continue
            print(f"\n--- role={role} (showing first 12 of {len(els)}) ---")
            for el in els[:12]:
                try:
                    aria = (el.get_attribute("aria-label") or "")[:60]
                    text = (el.inner_text() or "").strip().replace("\n", " | ")[:90]
                    if aria or text:
                        print(f"  aria={aria!r}  text={text!r}")
                except Exception:
                    pass

        # Reply textbox candidates
        print("\n--- textboxes (potential reply input) ---")
        for tb in page.get_by_role("textbox").all():
            try:
                aria = tb.get_attribute("aria-label") or "(none)"
                ph = tb.get_attribute("placeholder") or "(none)"
                print(f"  aria={aria!r}  placeholder={ph!r}")
            except Exception:
                pass


# Backwards-compat: support `python -m src.diag` directly with a positional arg
if __name__ == "__main__":
    import sys
    from . import platforms as _platforms

    if len(sys.argv) >= 2:
        name = sys.argv[1]
    else:
        name = "facebook"
    dump_inbox(_platforms.get(name))
