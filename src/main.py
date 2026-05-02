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

from . import browser, classifier, config, db, notifier, platforms, responder


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
            try:
                _cycle(platform, cfg, client)
            except Exception as e:
                click.echo(f"[{platform.name} cycle error] {e}")

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
            inbound_all, threads_scanned = platform.poll_inbox(ctx)
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
                            try:
                                db.insert_appointment(
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
