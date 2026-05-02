"""Decide auto-send vs queue for review."""
from __future__ import annotations

from .config import Config


def match_listing(thread_listing_title: str | None, listings: list[dict]) -> dict | None:
    if not thread_listing_title:
        return listings[0] if len(listings) == 1 else None
    title = thread_listing_title.lower()
    for L in listings:
        if L["title_match"].lower() in title:
            return L
    return listings[0] if len(listings) == 1 else None


def should_auto_send(cfg: Config, inbound_body: str, draft_body: str) -> tuple[bool, str]:
    """Returns (auto_send, reason). reason is logged on the draft."""
    if cfg.send_mode == "auto":
        return True, "auto mode"
    if cfg.send_mode == "draft":
        return False, "draft mode"

    # hybrid: queue if any escalation trigger appears in either side
    blob = (inbound_body + "\n" + draft_body).lower()
    for trigger in cfg.escalation_triggers:
        if trigger in blob:
            return False, f"trigger: {trigger}"
    return True, "hybrid: no triggers matched"
