"""Dump anchor hrefs on the marketplace inbox so we can find the right pattern."""
from __future__ import annotations
from collections import Counter
from . import browser, poller


def main():
    with browser.context(headless=False) as ctx:
        page = ctx.new_page()
        page.goto(poller.INBOX_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(12_000)
        print(f"url: {page.url}")
        print(f"total <a>: {page.locator('a').count()}")

        # Histogram of href prefixes
        prefixes = Counter()
        for a in page.locator('a').all():
            try:
                href = a.get_attribute("href") or ""
                if not href:
                    continue
                # Bucket by first 30 chars
                prefixes[href[:30]] += 1
            except Exception:
                pass
        print("\nhref prefix histogram (top 20):")
        for pre, n in prefixes.most_common(20):
            print(f"  {n:3d}  {pre}")

        # Sample 20 anchors with non-empty aria or text
        print("\nsample anchors with aria-label or text:")
        shown = 0
        for a in page.locator('a').all():
            try:
                href = a.get_attribute("href") or ""
                aria = a.get_attribute("aria-label") or ""
                text = (a.inner_text() or "").strip().replace("\n", " | ")[:80]
                if aria or text:
                    print(f"  href={href[:80]} aria={aria[:60]!r} text={text!r}")
                    shown += 1
                    if shown >= 20:
                        break
            except Exception:
                pass


if __name__ == "__main__":
    main()
