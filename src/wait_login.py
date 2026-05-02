"""Open FB inbox in a browser window so the user can complete a security
challenge (PIN, captcha, etc). Stays open for 5 minutes."""
from __future__ import annotations

import time

from . import browser, poller


def main():
    print("Opening Facebook inbox. Complete any security challenge in the window.")
    print("This window will stay open for 5 minutes.")
    with browser.context(headless=False) as ctx:
        page = ctx.new_page()
        page.goto(poller.INBOX_URL, wait_until="domcontentloaded")
        for remaining in range(300, 0, -30):
            print(f"  ...{remaining}s remaining; current url: {page.url}")
            time.sleep(30)
        print("Time up. Closing window.")


if __name__ == "__main__":
    main()
