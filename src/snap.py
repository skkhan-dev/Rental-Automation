"""Screenshot the inbox so we can see what's actually rendering."""
from __future__ import annotations
from . import browser, poller


def main():
    with browser.context(headless=False) as ctx:
        page = ctx.new_page()
        page.goto(poller.INBOX_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(12_000)
        path = "data/inbox.png"
        page.screenshot(path=path, full_page=True)
        print(f"saved {path}")
        print(f"final url: {page.url}")
        print(f"title: {page.title()}")
        sel = 'a[href*="/messages/t/"]'
        n = page.locator(sel).count()
        print(f"anchors with /messages/t/: {n}")


if __name__ == "__main__":
    main()
