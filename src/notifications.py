"""SMS / email / voice-call helpers for appointment notifications.

All three send-functions return (ok: bool, detail: str). They gracefully
no-op when the relevant credentials aren't configured — no exception
bubbles up to break the cycle.

Credentials live in environment variables loaded from .env (see
.env.example for the keys).
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


# ── Twilio (SMS + voice) ────────────────────────────────────────────────

def _twilio_client():
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    tok = os.getenv("TWILIO_AUTH_TOKEN")
    if not (sid and tok):
        return None
    try:
        from twilio.rest import Client
    except ImportError:
        return None
    return Client(sid, tok)


def _twilio_from() -> str | None:
    n = os.getenv("TWILIO_FROM_NUMBER", "").strip()
    return n or None


def send_sms(to: str, body: str) -> tuple[bool, str]:
    if not to:
        return False, "missing recipient phone"
    client = _twilio_client()
    if not client:
        return False, "Twilio not configured (set TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN)"
    from_ = _twilio_from()
    if not from_:
        return False, "TWILIO_FROM_NUMBER not set"
    try:
        msg = client.messages.create(to=to, from_=from_, body=body)
        return True, f"sid={msg.sid}"
    except Exception as e:
        return False, f"twilio: {e}"


def place_call(to: str, message: str) -> tuple[bool, str]:
    """Outbound voice call with a TwiML <Say> body. Charges per minute."""
    if not to:
        return False, "missing recipient phone"
    client = _twilio_client()
    if not client:
        return False, "Twilio not configured"
    from_ = _twilio_from()
    if not from_:
        return False, "TWILIO_FROM_NUMBER not set"
    # Strip out anything that could break the TwiML XML; keep it simple
    safe = (message or "").replace("&", " and ").replace("<", " ").replace(">", " ")
    twiml = (
        '<Response><Say voice="alice">'
        f'{safe}'
        ' This message will repeat once.'
        '</Say>'
        '<Pause length="1"/>'
        f'<Say voice="alice">{safe}</Say>'
        '</Response>'
    )
    try:
        call = client.calls.create(to=to, from_=from_, twiml=twiml)
        return True, f"sid={call.sid}"
    except Exception as e:
        return False, f"twilio: {e}"


# ── Email via SMTP ──────────────────────────────────────────────────────

def send_email(to: str, subject: str, body: str) -> tuple[bool, str]:
    if not to:
        return False, "missing recipient email"
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "").strip()
    pwd = os.getenv("SMTP_PASSWORD", "")
    from_ = os.getenv("SMTP_FROM", "").strip() or user
    if not (user and pwd):
        return False, "SMTP not configured (set SMTP_USER / SMTP_PASSWORD)"
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_
        msg["To"] = to
        msg.set_content(body)
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            s.login(user, pwd)
            s.send_message(msg)
        return True, "ok"
    except Exception as e:
        return False, f"smtp: {e}"


# ── Convenience: notify about an appointment ────────────────────────────

def appointment_summary_text(appt: dict) -> str:
    """Plain-text summary used as both the SMS body and the email body."""
    parts = []
    if appt.get("counterparty"):
        parts.append(f"Prospect: {appt['counterparty']}")
    if appt.get("listing_title"):
        parts.append(f"Property: {appt['listing_title']}")
    if appt.get("when_text"):
        parts.append(f"When: {appt['when_text']}")
    if appt.get("phone"):
        parts.append(f"Phone: {appt['phone']}")
    if appt.get("status"):
        parts.append(f"Status: {appt['status']}")
    return "\n".join(parts)


def appointment_subject(appt: dict) -> str:
    who = appt.get("counterparty") or "applicant"
    when = appt.get("when_text") or "TBD"
    return f"[Rental] Viewing scheduled — {who} ({when})"


def dispatch(
    *,
    channel: str,                  # sms | email | call
    recipient: str,                # tenant | owner
    appt: dict,
    owner: dict,
    body: str | None = None,
) -> tuple[bool, str, str | None]:
    """Send one notification. Returns (ok, detail, target_used).

    Resolves the target (phone or email) from `recipient`:
      - tenant: appt['phone'] (no email — we don't ask tenants for email)
      - owner:  owner['phone'] for sms/call, owner['email'] for email
    """
    text = body or appointment_summary_text(appt)
    subject = appointment_subject(appt)

    if recipient == "tenant":
        target = (appt.get("phone") or "").strip()
        if channel == "email":
            return False, "we don't store tenant email addresses", None
    elif recipient == "owner":
        if channel == "email":
            target = (owner.get("email") or "").strip()
        else:
            target = (owner.get("phone") or "").strip()
    else:
        return False, f"unknown recipient: {recipient}", None

    if not target:
        return False, f"no target {channel} for {recipient}", None

    if channel == "sms":
        ok, detail = send_sms(target, text)
    elif channel == "email":
        ok, detail = send_email(target, subject, text)
    elif channel == "call":
        ok, detail = place_call(target, text)
    else:
        return False, f"unknown channel: {channel}", target

    return ok, detail, target
