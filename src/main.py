"""CLI entry point."""
from __future__ import annotations

import os
import random
import time

import anthropic
import click
from dotenv import load_dotenv

from . import browser, classifier, config, db, notifier, poller, responder, sender


@click.group()
def cli():
    """Facebook Marketplace auto-reply tool."""
    load_dotenv(override=True)


@cli.command()
def login():
    """Open Facebook in a window so you can log in. Cookies persist."""
    click.echo("Opening Facebook. Log in (and complete 2FA), then close the window.")
    with browser.context(headless=False) as ctx:
        page = ctx.new_page()
        page.goto("https://www.facebook.com/login")
        click.echo("Press Ctrl+C in this terminal when you're done logging in.")
        try:
            while True:
                time.sleep(2)
        except KeyboardInterrupt:
            click.echo("\nSaved.")


@cli.command()
def inspect():
    """Open the Marketplace inbox and pause. Useful for tuning selectors."""
    cfg = config.load()
    with browser.context(headless=False) as ctx:
        page = ctx.new_page()
        page.goto(poller.INBOX_URL)
        click.echo("Inbox open. Inspect with devtools. Ctrl+C to exit.")
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            pass


@cli.command()
@click.option("--once", is_flag=True, help="Run one poll cycle and exit.")
def run(once: bool):
    """Poll the inbox, draft replies, send or queue per send_mode."""
    cfg = config.load()
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise click.ClickException("ANTHROPIC_API_KEY not set (see .env.example)")

    client = anthropic.Anthropic()

    while True:
        try:
            _cycle(cfg, client)
        except Exception as e:
            click.echo(f"[cycle error] {e}")

        if once:
            break
        click.echo(f"sleeping {cfg.poll_interval_seconds}s")
        time.sleep(cfg.poll_interval_seconds)


def _cycle(cfg: config.Config, client: anthropic.Anthropic):
    with db.conn(config.DB_PATH) as c:
        cycle_id = db.cycle_start(c)

    sent = 0
    queued = 0
    threads_scanned = 0
    unread_found = 0
    error_msg: str | None = None

    try:
        with browser.context(headless=False) as ctx, db.conn(config.DB_PATH) as c:
            inbound_all, threads_scanned = poller.poll_inbox(ctx)
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

                db.upsert_thread(c, m.thread_id, m.listing_title, m.counterparty)
                db.insert_message(c, m.msg_id, m.thread_id, "in", m.body)

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
                        sender.open_thread(page, m.thread_id)
                        sender.send_reply(page, reply, cfg.typing_delay_ms)
                        out_id = f"{m.thread_id}::out::{int(time.time())}"
                        db.insert_message(c, out_id, m.thread_id, "out", reply)
                        db.insert_draft(c, m.thread_id, m.msg_id, reply, "sent", reason)
                        sent += 1
                        if viewing:
                            notifier.notify_viewing(viewing, m.thread_id)
                            click.echo(f"  ⚑ viewing scheduled: {viewing.raw}")
                        time.sleep(random.uniform(*cfg.delay_between_replies_seconds))
                    except Exception as e:
                        click.echo(f"  [send error] {e}; queuing instead")
                        db.insert_draft(c, m.thread_id, m.msg_id, reply, "pending", f"send failed: {e}")
                        queued += 1
                else:
                    db.insert_draft(c, m.thread_id, m.msg_id, reply, "pending", reason)
                    queued += 1
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        click.echo(f"[cycle error] {error_msg}")

    # Decide overall status and persist cycle summary
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
        # Session-health: if the last 2 cycles both saw 0 threads on the inbox,
        # FB has likely logged us out or thrown a security challenge.
        recent = db.list_cycles(c, limit=2)
        if len(recent) >= 2 and all(r["threads_scanned"] == 0 for r in recent):
            from . import notifier as _n
            _n.notify_session_unhealthy(
                f"Last 2 cycles saw 0 threads (latest status: {recent[0]['status']})"
            )
            click.echo("⚠️ session unhealthy — notification fired")


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

        client = anthropic.Anthropic() if os.getenv("ANTHROPIC_API_KEY") else None

        with browser.context(headless=False) as ctx:
            page = ctx.new_page()
            for d in drafts:
                click.echo("\n" + "=" * 70)
                click.echo(f"thread: {d['thread_id']}  ({d['counterparty']})")
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
                # send
                try:
                    sender.open_thread(page, d["thread_id"])
                    sender.send_reply(page, body, cfg.typing_delay_ms)
                    out_id = f"{d['thread_id']}::out::{int(time.time())}"
                    db.insert_message(c, out_id, d["thread_id"], "out", body)
                    db.mark_draft(c, d["id"], "sent")
                    click.echo("sent.")
                except Exception as e:
                    click.echo(f"send failed: {e}")


if __name__ == "__main__":
    cli()
