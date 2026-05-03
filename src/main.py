"""CLI entry point."""
from __future__ import annotations

import os
import random
import re
import time
from datetime import datetime


# Pull a YYYY-MM-DD out of arbitrary date strings the AI may emit
# ("Sat 5/4 at 6pm", "May 4 6:30 PM", "2026-05-04 18:00", etc.)
_DATE_PATTERNS = [
    re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"),  # ISO
    re.compile(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?"),  # MM/DD or MM/DD/YYYY
]


def _resolve_contact(cfg: "config.Config", listing_title: str | None) -> tuple[dict, list[str]]:
    """Pick the right contact + channel set for an appointment.

    1. Try to match the appointment's listing to one in listings.yaml.
       If that listing has a `contact_*` block, use it.
    2. Fall back to the global config.yaml `owner` block.

    Returns (contact_dict, channels) where contact_dict has
    {name, email, phone} and channels is the list of preferred channels
    (e.g. ['sms', 'email']).
    """
    listing = classifier.match_listing(listing_title, cfg.listings) or {}

    # Per-listing contact if available
    has_listing_contact = any(
        listing.get(k) for k in ("contact_name", "contact_email", "contact_phone")
    )
    if has_listing_contact:
        contact = {
            "name":  listing.get("contact_name"),
            "email": listing.get("contact_email"),
            "phone": listing.get("contact_phone"),
        }
        pref = (listing.get("contact_preference") or "sms,email").lower()
        channels = [c.strip() for c in pref.split(",") if c.strip() in ("sms", "email", "call")]
        return contact, channels or ["sms"]

    # Fallback to global owner
    owner = cfg.owner or {}
    flags = owner.get("notify_on_create") or {}
    channels = [ch for ch in ("sms", "email", "call") if flags.get(ch)]
    return owner, channels


def _auto_notify_owner(c, cfg: "config.Config", appt_id: int) -> None:
    """Fire owner-side notifications when a viewing is booked.

    Looks up the per-listing contact first; falls back to global owner.
    """
    appt_row = db.get_appointment(c, appt_id)
    if not appt_row:
        return
    appt = dict(appt_row)
    contact, channels = _resolve_contact(cfg, appt.get("listing_title"))
    if not channels:
        return
    for channel in channels:
        ok, detail, target = notifications.dispatch(
            channel=channel,
            recipient="owner",
            appt=appt,
            owner=contact,
        )
        db.log_notification(
            c,
            appointment_id=appt_id,
            channel=channel,
            recipient="owner",
            target=target,
            status="sent" if ok else "failed",
            detail=f"{contact.get('name') or '(no name)'} — {detail}",
        )


def _platform_interval(name: str, cfg: "config.Config") -> tuple[int, int]:
    """Return (min_s, max_s) for the platform's polling interval.

    Falls back to the global schedule's min/max if the platform doesn't
    appear in cfg.platform_intervals.
    """
    overrides = cfg.platform_intervals or {}
    v = overrides.get(name)
    if isinstance(v, list) and len(v) == 2:
        try:
            return int(v[0]), int(v[1])
        except (TypeError, ValueError):
            pass
    sched = cfg.schedule or {}
    return (
        int(sched.get("min_interval_seconds", 1200)),
        int(sched.get("max_interval_seconds", 3000)),
    )


def _platform_next_run_path(name: str):
    return config.DATA_DIR / f"next_run_{name}.txt"


def _platform_due(name: str) -> bool:
    """True if this platform's interval has elapsed (or never run)."""
    p = _platform_next_run_path(name)
    if not p.exists():
        return True
    try:
        next_ts = int(p.read_text().strip())
    except Exception:
        return True
    return time.time() >= next_ts


def _platform_schedule_next(name: str, cfg: "config.Config") -> int:
    """Pick a random next-run timestamp inside the platform's interval window."""
    lo, hi = _platform_interval(name, cfg)
    if hi < lo:
        lo, hi = hi, lo
    delay = random.randint(max(1, lo), max(1, hi))
    next_ts = int(time.time()) + delay
    p = _platform_next_run_path(name)
    p.parent.mkdir(exist_ok=True)
    p.write_text(str(next_ts))
    return next_ts


def _parse_iso_date(s: str | None) -> str | None:
    if not s:
        return None
    # Try strict ISO first
    m = _DATE_PATTERNS[0].search(s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date().isoformat()
        except Exception:
            pass
    # Try MM/DD with current/inferred year
    m = _DATE_PATTERNS[1].search(s)
    if m:
        try:
            month, day = int(m.group(1)), int(m.group(2))
            year = int(m.group(3)) if m.group(3) else datetime.now().year
            if year < 100:
                year += 2000
            return datetime(year, month, day).date().isoformat()
        except Exception:
            pass
    return None

import anthropic
import click
from dotenv import load_dotenv

from . import browser, classifier, config, db, notifications, notifier, platforms, responder


@click.group()
def cli():
    """Rental Marketplace auto-reply tool."""
    load_dotenv(override=True)


@cli.command()
@click.option(
    "--platform",
    "platform_name",
    default="facebook",
    type=click.Choice(list(platforms.REGISTRY.keys())),
    help="Which platform to log into",
)
def login(platform_name: str):
    """Open a platform's login page so you can authenticate. Cookies persist."""
    p = platforms.get(platform_name)
    click.echo(f"Opening {p.name} login. Log in (handle any challenges), then Ctrl+C in this terminal.")
    with browser.context(headless=False) as ctx:
        page = ctx.new_page()
        page.goto(p.login_url)
        try:
            while True:
                time.sleep(2)
        except KeyboardInterrupt:
            click.echo("\nSaved.")


@cli.command()
@click.option(
    "--platform",
    "platform_name",
    default="facebook",
    type=click.Choice(list(platforms.REGISTRY.keys())),
)
def inspect(platform_name: str):
    """Open a platform's inbox and pause. Useful for tuning selectors."""
    config.load()
    p = platforms.get(platform_name)
    with browser.context(headless=False) as ctx:
        page = ctx.new_page()
        page.goto(p.inbox_url)
        click.echo(f"{p.name} inbox open at {p.inbox_url}. Ctrl+C to exit.")
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            pass


@cli.command()
@click.option(
    "--platform",
    "platform_name",
    required=True,
    type=click.Choice(list(platforms.REGISTRY.keys())),
)
def diag(platform_name: str):
    """Dump a platform's inbox DOM so we can write/fix selectors."""
    from . import diag as diag_mod
    p = platforms.get(platform_name)
    diag_mod.dump_inbox(p)


@cli.command()
@click.option("--once", is_flag=True, help="Run one poll cycle and exit.")
def run(once: bool):
    """Poll each enabled platform's inbox and draft / send replies."""
    cfg = config.load()
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise click.ClickException("ANTHROPIC_API_KEY not set (see .env.example)")

    client = anthropic.Anthropic()

    while True:
        for platform in platforms.enabled_platforms():
            if not _platform_due(platform.name):
                lo, hi = _platform_interval(platform.name, cfg)
                next_ts = _platform_next_run_path(platform.name).read_text().strip()
                wait_s = max(0, int(next_ts) - int(time.time()))
                click.echo(
                    f"\n=== {platform.name} skipped (interval {lo}-{hi}s; "
                    f"next due in {wait_s}s) ==="
                )
                continue
            try:
                _cycle(platform, cfg, client)
            except Exception as e:
                click.echo(f"[{platform.name} cycle error] {e}")
            finally:
                # Schedule next run regardless of success — failures shouldn't
                # cause us to retry every fire and burn through the API.
                _platform_schedule_next(platform.name, cfg)

        if once:
            break
        click.echo(f"sleeping {cfg.poll_interval_seconds}s")
        time.sleep(cfg.poll_interval_seconds)


def _cycle(platform: platforms.Platform, cfg: config.Config, client: anthropic.Anthropic):
    click.echo(f"\n=== {platform.name} cycle ===")
    with db.conn(config.DB_PATH) as c:
        cycle_id = db.cycle_start(c, platform=platform.name)

    sent = 0
    queued = 0
    threads_scanned = 0
    unread_found = 0
    error_msg: str | None = None

    try:
        with browser.context(headless=False) as ctx, db.conn(config.DB_PATH) as c:
            try:
                inbound_all, threads_scanned = platform.poll_inbox(ctx)
            except platforms.BotChallengeDetected as bc:
                # Pause this platform's cycle, page the user, exit cleanly.
                error_msg = f"bot_challenge: {bc}"
                click.echo(f"  🚨 {error_msg}")
                notifier.notify_bot_challenge(platform.name, str(bc), owner=cfg.owner)
                with db.conn(config.DB_PATH) as c2:
                    db.cycle_end(
                        c2, cycle_id, status="failure",
                        threads_scanned=0, unread_found=0,
                        replies_sent=0, replies_queued=0,
                        error_msg=error_msg,
                    )
                return  # done with this platform's cycle
            unread_found = len(inbound_all)
            click.echo(f"found {len(inbound_all)} unread inbound messages")
            inbound = inbound_all[: cfg.cycle_cap]
            if len(inbound) < len(inbound_all):
                click.echo(
                    f"  capping to {cfg.cycle_cap} this cycle; "
                    f"{len(inbound_all) - len(inbound)} will roll to next cycle"
                )

            page = ctx.new_page()

            for m in inbound:
                if db.message_seen(c, m.msg_id):
                    continue

                db.upsert_thread(c, m.thread_id, m.listing_title, m.counterparty, platform=platform.name)
                db.insert_message(c, m.msg_id, m.thread_id, "in", m.body, platform=platform.name)

                listing = classifier.match_listing(m.listing_title, cfg.listings)
                if not listing:
                    click.echo(f"  [skip] no listing match for thread {m.thread_id}")
                    continue

                history = [
                    {"direction": r["direction"], "body": r["body"]}
                    for r in db.thread_history(c, m.thread_id)
                ]
                history = history[:-1] if history else []

                try:
                    reply = responder.draft_reply(client, cfg, listing, history, m.body)
                except Exception as e:
                    click.echo(f"  [draft error] {e}")
                    continue
                if not reply:
                    continue

                viewing = notifier.parse(reply)
                if viewing:
                    reply = notifier.strip_marker(reply)

                auto, reason = classifier.should_auto_send(cfg, m.body, reply)
                click.echo(f"  thread={m.thread_id[:12]} auto={auto} reason={reason}")
                click.echo(f"    draft: {reply[:120]}")

                if auto:
                    try:
                        platform.open_thread(page, m.thread_id)
                        platform.send_reply(page, reply, cfg.typing_delay_ms)
                        out_id = f"{m.thread_id}::out::{int(time.time())}"
                        db.insert_message(c, out_id, m.thread_id, "out", reply, platform=platform.name)
                        db.insert_draft(c, m.thread_id, m.msg_id, reply, "sent", reason, platform=platform.name)
                        sent += 1
                        if viewing:
                            notifier.notify_viewing(viewing, m.thread_id)
                            appt_id = None
                            try:
                                appt_id = db.insert_appointment(
                                    c,
                                    platform=platform.name,
                                    thread_id=m.thread_id,
                                    counterparty=viewing.tenant,
                                    phone=viewing.phone if viewing.phone != "unknown" else None,
                                    listing_title=viewing.property if viewing.property != "unknown" else m.listing_title,
                                    when_text=viewing.when if viewing.when != "unknown" else None,
                                    when_date=_parse_iso_date(viewing.when),
                                )
                            except Exception as e:
                                click.echo(f"  [appt insert error] {e}")
                            click.echo(f"  ⚑ viewing scheduled: {viewing.raw}")

                            # Auto-notify the owner per config.owner.notify_on_create
                            if appt_id:
                                _auto_notify_owner(c, cfg, appt_id)
                        time.sleep(random.uniform(*cfg.delay_between_replies_seconds))
                    except Exception as e:
                        click.echo(f"  [send error] {e}; queuing instead")
                        db.insert_draft(
                            c, m.thread_id, m.msg_id, reply, "pending",
                            f"send failed: {e}", platform=platform.name,
                        )
                        queued += 1
                else:
                    db.insert_draft(c, m.thread_id, m.msg_id, reply, "pending", reason, platform=platform.name)
                    queued += 1
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        click.echo(f"[cycle error] {error_msg}")

    if error_msg:
        status = "failure"
    elif unread_found > 0 and sent == 0 and queued == 0:
        status = "partial"
    else:
        status = "success"

    with db.conn(config.DB_PATH) as c:
        db.cycle_end(
            c,
            cycle_id,
            status=status,
            threads_scanned=threads_scanned,
            unread_found=unread_found,
            replies_sent=sent,
            replies_queued=queued,
            error_msg=error_msg,
        )
        # Session-health, scoped per platform.
        recent = db.list_cycles(c, limit=2, platform=platform.name)
        if len(recent) >= 2 and all(r["threads_scanned"] == 0 for r in recent):
            notifier.notify_session_unhealthy(
                f"{platform.name}: last 2 cycles saw 0 threads "
                f"(latest status: {recent[0]['status']})"
            )
            click.echo(f"⚠️ {platform.name} session unhealthy — notification fired")


@cli.command()
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=8765, type=int)
def dashboard(host: str, port: int):
    """Launch the web dashboard for reviewing drafts."""
    import uvicorn

    from .dashboard import app

    config.load()  # validate config before serving
    if not os.getenv("ANTHROPIC_API_KEY"):
        click.echo("warning: ANTHROPIC_API_KEY not set — sends will work but new drafts won't")
    click.echo(f"dashboard at http://{host}:{port}/")
    uvicorn.run(app, host=host, port=port, log_level="warning")


@cli.command()
def review():
    """Review pending drafts in the terminal."""
    cfg = config.load()
    with db.conn(config.DB_PATH) as c:
        drafts = db.pending_drafts(c)
        if not drafts:
            click.echo("no pending drafts")
            return

        with browser.context(headless=False) as ctx:
            page = ctx.new_page()
            for d in drafts:
                pname = d["platform"] or "facebook"
                platform = platforms.get(pname)
                click.echo("\n" + "=" * 70)
                click.echo(f"[{pname}] thread: {d['thread_id']}  ({d['counterparty']})")
                click.echo(f"reason: {d['reason']}")
                click.echo(f"draft:\n{d['body']}")
                action = click.prompt(
                    "[s]end / [e]dit / [r]eject / [k]eep / [q]uit",
                    default="k",
                ).lower()
                if action == "q":
                    break
                if action == "k":
                    continue
                if action == "r":
                    db.mark_draft(c, d["id"], "rejected")
                    continue
                body = d["body"]
                if action == "e":
                    body = click.edit(body) or body
                try:
                    platform.open_thread(page, d["thread_id"])
                    platform.send_reply(page, body, cfg.typing_delay_ms)
                    out_id = f"{d['thread_id']}::out::{int(time.time())}"
                    db.insert_message(c, out_id, d["thread_id"], "out", body, platform=pname)
                    db.mark_draft(c, d["id"], "sent")
                    click.echo("sent.")
                except Exception as e:
                    click.echo(f"send failed: {e}")


if __name__ == "__main__":
    cli()
