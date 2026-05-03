"""Detect [VIEWING_CONFIRMED] markers in drafts and notify the landlord.

Notification channels (any/all are tried; all are best-effort):
  1. macOS native notification banner via osascript (always available locally)
  2. Append to data/viewings.log for posterity
  3. (TODO) Email via SMTP when SMTP creds exist in .env
"""
from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR

VIEWINGS_LOG = DATA_DIR / "viewings.log"
_MARKER_RE = re.compile(r"\[VIEWING_CONFIRMED\][^\n]*", re.IGNORECASE)
_FIELD_RE = re.compile(r"(\w+)=((?:[^\s=]|\s+(?!\w+=))+)")


@dataclass
class Viewing:
    tenant: str
    property: str
    when: str
    phone: str
    raw: str


def parse(reply_text: str) -> Viewing | None:
    """Pull viewing details out of the [VIEWING_CONFIRMED] marker line, if present."""
    m = _MARKER_RE.search(reply_text)
    if not m:
        return None
    line = m.group(0)
    fields = {k.lower(): v.strip() for k, v in _FIELD_RE.findall(line)}
    return Viewing(
        tenant=fields.get("tenant", "unknown"),
        property=fields.get("property", "unknown"),
        when=fields.get("when", "unknown"),
        phone=fields.get("phone", "unknown"),
        raw=line,
    )


def strip_marker(reply_text: str) -> str:
    """Remove the [VIEWING_CONFIRMED] line so it isn't sent to the tenant."""
    cleaned = _MARKER_RE.sub("", reply_text)
    # Collapse double-blank lines created by removal
    return "\n".join(line for line in cleaned.splitlines() if line.strip()) + "\n"


def _macos_notify(title: str, body: str) -> None:
    """Pop a banner on the user's Mac. Best-effort; silent on failure."""
    try:
        # Use osascript with safely-quoted args
        script = (
            f'display notification {shlex.quote(body)} '
            f'with title {shlex.quote(title)} sound name "Submarine"'
        )
        subprocess.run(["osascript", "-e", script], check=False, timeout=5)
    except Exception:
        pass


def _append_log(v: Viewing) -> None:
    VIEWINGS_LOG.parent.mkdir(exist_ok=True)
    with VIEWINGS_LOG.open("a") as f:
        ts = datetime.now().isoformat(timespec="seconds")
        f.write(
            f"{ts}\ttenant={v.tenant}\tproperty={v.property}\twhen={v.when}\tphone={v.phone}\n"
        )


def notify_viewing(v: Viewing, thread_id: str) -> None:
    title = "🏠 Viewing scheduled"
    body = f"{v.tenant} · {v.property} · {v.when} · {v.phone}"
    _macos_notify(title, body)
    _append_log(v)


def notify_session_unhealthy(reason: str) -> None:
    """Fire when the bot can't see the inbox — likely needs re-login."""
    _macos_notify(
        "⚠️ FB Auto-Reply: session check failed",
        f"{reason} — open Messenger and log in / handle any FB challenge.",
    )


# ── Bot-challenge alerting (with cooldown) ─────────────────────────────

def _challenge_cooldown_path(platform: str):
    return DATA_DIR / f"challenge_cooldown_{platform}.txt"


def _challenge_cooldown_active(platform: str, seconds: int = 3600) -> bool:
    """True if we already fired a challenge alert for this platform recently."""
    p = _challenge_cooldown_path(platform)
    if not p.exists():
        return False
    try:
        last = int(p.read_text().strip())
    except Exception:
        return False
    import time as _t
    return (_t.time() - last) < seconds


def _challenge_cooldown_set(platform: str) -> None:
    import time as _t
    p = _challenge_cooldown_path(platform)
    p.parent.mkdir(exist_ok=True)
    p.write_text(str(int(_t.time())))


def notify_bot_challenge(platform: str, detail: str, owner: dict | None = None) -> None:
    """Macos banner immediately; SMS the owner at most once per hour per
    platform so a stuck cycle doesn't blast the phone every 10 minutes."""
    title = f"🚨 {platform}: bot challenge"
    body = (
        f"{detail}\n"
        f"Open the persistent browser profile and complete the challenge "
        f"(captcha / PIN / verify-you-are-human) so polling can resume."
    )
    _macos_notify(title, body)

    # Append to viewings.log too — useful for forensics
    try:
        VIEWINGS_LOG.parent.mkdir(exist_ok=True)
        with VIEWINGS_LOG.open("a") as f:
            ts = datetime.now().isoformat(timespec="seconds")
            f.write(f"{ts}\tCHALLENGE\t{platform}\t{detail}\n")
    except Exception:
        pass

    # Optional SMS to owner — cooldown 1 hour per platform
    if not owner:
        return
    if _challenge_cooldown_active(platform):
        return
    phone = (owner.get("phone") or "").strip()
    if not phone:
        return
    try:
        from . import notifications as _n
        ok, ndetail = _n.send_sms(
            phone,
            f"[Rental] {platform} needs you. {detail[:120]}. Polling paused on this platform until you handle it.",
        )
        if ok:
            _challenge_cooldown_set(platform)
    except Exception:
        pass
