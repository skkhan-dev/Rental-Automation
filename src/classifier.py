"""Match a thread to a listing; decide auto-send vs queue for review."""
from __future__ import annotations

import re

from .config import Config


# Common address-style abbreviations Avail / Zillow / FB use interchangeably.
# Only matched at word boundaries so we don't mangle proper nouns
# (e.g. "St. James" stays intact because we only handle "St" / "St." → "Street"
# when the next char isn't a letter that would form a real name).
_ABBREVIATIONS = [
    (re.compile(r"\bS\.?\b", re.IGNORECASE), "South"),
    (re.compile(r"\bN\.?\b", re.IGNORECASE), "North"),
    (re.compile(r"\bE\.?\b", re.IGNORECASE), "East"),
    (re.compile(r"\bW\.?\b", re.IGNORECASE), "West"),
    (re.compile(r"\bSt\.?\b", re.IGNORECASE), "Street"),
    (re.compile(r"\bAve\.?\b", re.IGNORECASE), "Avenue"),
    (re.compile(r"\bBlvd\.?\b", re.IGNORECASE), "Boulevard"),
    (re.compile(r"\bApt\.?\b", re.IGNORECASE), "Apartment"),
    (re.compile(r"\bRd\.?\b", re.IGNORECASE), "Road"),
    (re.compile(r"\bDr\.?\b", re.IGNORECASE), "Drive"),
    (re.compile(r"\bCt\.?\b", re.IGNORECASE), "Court"),
    (re.compile(r"\bLn\.?\b", re.IGNORECASE), "Lane"),
    (re.compile(r"\bPl\.?\b", re.IGNORECASE), "Place"),
    (re.compile(r"\bPkwy\.?\b", re.IGNORECASE), "Parkway"),
]


def _normalize(s: str) -> str:
    """Lower-case and expand street/direction abbreviations.
    Allows '59 S 5th St' to match '59 South 5th Street'."""
    s = s.lower()
    for pat, repl in _ABBREVIATIONS:
        s = pat.sub(repl.lower(), s)
    return re.sub(r"\s+", " ", s).strip()


def _is_in_cycle(L: dict) -> bool:
    """A listing participates in the cycle only when its lifecycle is 'active'.

    'pause' and 'archive' both cause the matcher to skip. The legacy
    `active: bool` field is honored for backward compat.
    """
    lc = (L.get("lifecycle") or "").strip().lower()
    if lc:
        return lc == "active"
    if "active" in L:
        return bool(L["active"])
    return True  # default for entries with neither field


def match_listing(thread_listing_title: str | None, listings: list[dict]) -> dict | None:
    """Match a thread to a listing.

    Listings whose lifecycle is 'pause' or 'archive' are skipped — the
    matcher behaves as if they don't exist, so threads about those
    listings fall through to "no listing match" and the cycle skips.
    """
    active_listings = [L for L in listings if _is_in_cycle(L)]
    if not thread_listing_title:
        return active_listings[0] if len(active_listings) == 1 else None
    title_norm = _normalize(thread_listing_title)
    for L in active_listings:
        match_norm = _normalize(L.get("title_match", ""))
        if match_norm and match_norm in title_norm:
            return L
    return active_listings[0] if len(active_listings) == 1 else None


def should_auto_send(cfg: Config, inbound_body: str, draft_body: str) -> tuple[bool, str]:
    """Returns (auto_send, reason). reason is logged on the draft."""
    if cfg.send_mode == "auto":
        return True, "auto mode"
    if cfg.send_mode == "draft":
        return False, "draft mode"

    blob = (inbound_body + "\n" + draft_body).lower()
    for trigger in cfg.escalation_triggers:
        if trigger in blob:
            return False, f"trigger: {trigger}"
    return True, "hybrid: no triggers matched"
