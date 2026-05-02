"""FastAPI dashboard: drafts review + listings/guidelines editor.

Run with: python -m src.main dashboard
Then open http://127.0.0.1:8765/
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import yaml
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import browser, config, db, sender

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _datetimeformat(ts):
    from datetime import datetime
    if ts is None:
        return ""
    return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")


templates.env.filters["datetimeformat"] = _datetimeformat

app = FastAPI()
_cfg: config.Config | None = None

# Field schema for the listings editor — order, label, kind
_LISTING_FIELDS = [
    ("title_match", "Title match (substring of FB listing title)", "text"),
    ("status", "Status (available / rented)", "text"),
    ("name", "Display name", "text"),
    ("address", "Address", "text"),
    ("rent", "Rent ($/mo)", "int"),
    ("deposit", "Deposit ($)", "int"),
    ("available_from", "Available from", "text"),
    ("utilities", "Utilities", "text"),
    ("pets", "Pet policy", "text"),
    ("parking", "Parking", "text"),
    ("type", "Type / description", "textarea"),
    ("location_highlight", "Location highlight", "text"),
    ("landlord_phone", "Landlord phone", "text"),
    ("viewing_times", "Viewing times", "textarea"),
    ("redirect_to", "If rented — redirect tenant to:", "textarea"),
    ("notes", "Notes (internal)", "textarea"),
    ("custom_instructions", "Custom instructions for THIS listing (overrides generic guidance)", "textarea"),
]

LISTINGS_PATH = Path(__file__).resolve().parent.parent / "listings.yaml"
GUIDELINES_PATH = Path(__file__).resolve().parent.parent / "guidelines.md"
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def _config() -> config.Config:
    global _cfg
    if _cfg is None:
        _cfg = config.load()
    return _cfg


def _reload_config():
    """Force config reload after editing files."""
    global _cfg
    _cfg = None


# ── Drafts ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    cfg = _config()
    with db.conn(config.DB_PATH) as c:
        drafts = [dict(d) for d in db.pending_drafts(c)]
        for d in drafts:
            d["history"] = [
                dict(r) for r in db.thread_history(c, d["thread_id"], limit=12)
            ]
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "drafts": drafts, "send_mode": cfg.send_mode},
    )


@app.post("/drafts/{draft_id}/send")
def send_draft(draft_id: int, body: str = Form(...)):
    cfg = _config()
    with db.conn(config.DB_PATH) as c:
        row = c.execute(
            "SELECT thread_id FROM drafts WHERE id=? AND status='pending'", (draft_id,)
        ).fetchone()
        if not row:
            return RedirectResponse("/", status_code=303)
        thread_id = row["thread_id"]

        with browser.context(headless=False) as ctx:
            page = ctx.new_page()
            sender.open_thread(page, thread_id)
            sender.send_reply(page, body, cfg.typing_delay_ms)

        out_id = f"{thread_id}::out::{int(time.time())}"
        db.insert_message(c, out_id, thread_id, "out", body)
        c.execute(
            "UPDATE drafts SET body=?, status='sent', decided_ts=? WHERE id=?",
            (body, int(time.time()), draft_id),
        )
    return RedirectResponse("/", status_code=303)


@app.post("/drafts/{draft_id}/reject")
def reject_draft(draft_id: int):
    with db.conn(config.DB_PATH) as c:
        db.mark_draft(c, draft_id, "rejected")
    return RedirectResponse("/", status_code=303)


@app.post("/drafts/{draft_id}/save")
def save_draft(draft_id: int, body: str = Form(...)):
    with db.conn(config.DB_PATH) as c:
        c.execute("UPDATE drafts SET body=? WHERE id=?", (body, draft_id))
    return RedirectResponse("/", status_code=303)


# ── Listings editor ────────────────────────────────────────────────────

@app.get("/listings", response_class=HTMLResponse)
def listings_view(request: Request, saved: int = 0):
    doc = yaml.safe_load(LISTINGS_PATH.read_text()) or {}
    listings = doc.get("listings", [])
    return templates.TemplateResponse(
        "listings.html",
        {
            "request": request,
            "listings": listings,
            "fields": _LISTING_FIELDS,
            "saved": bool(saved),
        },
    )


@app.post("/listings/save")
async def listings_save(request: Request):
    form = await request.form()
    # Form fields are like: listings.0.title_match, listings.1.rent, etc.
    by_index: dict[int, dict] = {}
    for key, value in form.multi_items():
        if not key.startswith("listings."):
            continue
        try:
            _, idx_s, fname = key.split(".", 2)
            idx = int(idx_s)
        except (ValueError, IndexError):
            continue
        by_index.setdefault(idx, {})[fname] = value

    new_listings = []
    for idx in sorted(by_index):
        entry = by_index[idx]
        if entry.get("__delete") == "1":
            continue
        # Skip empty (added but not filled in) rows
        if not (entry.get("title_match") or entry.get("name")):
            continue
        cleaned = {}
        for fname, _label, kind in _LISTING_FIELDS:
            raw = entry.get(fname, "").strip()
            if not raw:
                continue
            if kind == "int":
                try:
                    cleaned[fname] = int(raw)
                except ValueError:
                    cleaned[fname] = raw
            else:
                cleaned[fname] = raw
        if cleaned:
            new_listings.append(cleaned)

    LISTINGS_PATH.write_text(
        "# Edited via dashboard at " + time.strftime("%Y-%m-%d %H:%M:%S") + "\n"
        + yaml.safe_dump({"listings": new_listings}, sort_keys=False, allow_unicode=True)
    )
    _reload_config()
    return RedirectResponse("/listings?saved=1", status_code=303)


@app.post("/listings/add")
def listings_add():
    """Add a blank listing row to the YAML file, then redirect back."""
    doc = yaml.safe_load(LISTINGS_PATH.read_text()) or {}
    listings = doc.get("listings", [])
    listings.append({"title_match": "", "status": "available", "name": "New listing"})
    LISTINGS_PATH.write_text(
        yaml.safe_dump({"listings": listings}, sort_keys=False, allow_unicode=True)
    )
    _reload_config()
    return RedirectResponse("/listings", status_code=303)


# ── History ────────────────────────────────────────────────────────────

@app.get("/history", response_class=HTMLResponse)
def history_view(request: Request, expand: Optional[int] = None):
    from datetime import datetime
    with db.conn(config.DB_PATH) as c:
        cycles = [dict(r) for r in db.list_cycles(c, limit=200)]
        expanded_drafts = []
        if expand is not None:
            expanded_drafts = [dict(r) for r in db.cycle_drafts(c, expand)]

    # Next-run timestamp (written by the launchd wrapper)
    next_run_epoch = None
    next_run_path = config.DATA_DIR / "next_run"
    if next_run_path.exists():
        try:
            next_run_epoch = int(next_run_path.read_text().strip())
        except Exception:
            pass

    next_run_human = None
    next_run_relative = None
    if next_run_epoch:
        next_run_human = datetime.fromtimestamp(next_run_epoch).strftime("%a %b %d, %I:%M %p")
        delta = next_run_epoch - int(time.time())
        if delta <= 0:
            next_run_relative = "now (next launchd tick)"
        else:
            mins = delta // 60
            next_run_relative = f"in {mins} min" if mins < 60 else f"in {mins // 60}h {mins % 60}m"

    last_cycle = cycles[0] if cycles else None

    return templates.TemplateResponse(
        "history.html",
        {
            "request": request,
            "cycles": cycles,
            "expand": expand,
            "expanded_drafts": expanded_drafts,
            "next_run_human": next_run_human,
            "next_run_relative": next_run_relative,
            "last_cycle": last_cycle,
        },
    )


@app.post("/history/{cycle_id}/delete")
def history_delete(cycle_id: int):
    with db.conn(config.DB_PATH) as c:
        db.delete_cycle(c, cycle_id)
    return RedirectResponse("/history", status_code=303)


@app.post("/history/clear")
def history_clear(scope: str = Form("all")):
    with db.conn(config.DB_PATH) as c:
        db.delete_all_cycles(c, only_failures=(scope == "failures"))
    return RedirectResponse("/history", status_code=303)


# ── Config editor ──────────────────────────────────────────────────────

@app.get("/config", response_class=HTMLResponse)
def config_view(request: Request, saved: int = 0):
    raw = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    return templates.TemplateResponse(
        "config.html",
        {"request": request, "cfg": raw, "saved": bool(saved)},
    )


@app.post("/config/save")
async def config_save(request: Request):
    form = await request.form()

    def _ival(key: str, default: int) -> int:
        try:
            return int(form.get(key, "").strip())
        except (TypeError, ValueError):
            return default

    triggers_raw = (form.get("escalation_triggers") or "").strip()
    triggers = [t.strip().lower() for t in triggers_raw.splitlines() if t.strip()]

    new_cfg = {
        "poll_interval_seconds": _ival("poll_interval_seconds", 180),
        "send_mode": (form.get("send_mode") or "draft").strip(),
        "cycle_cap": _ival("cycle_cap", 5),
        "escalation_triggers": triggers,
        "delay_between_replies_seconds": [
            _ival("delay_min_s", 90),
            _ival("delay_max_s", 240),
        ],
        "typing_delay_ms": [
            _ival("typing_min_ms", 40),
            _ival("typing_max_ms", 120),
        ],
        "model": (form.get("model") or "claude-sonnet-4-6").strip(),
        "schedule": {
            "start_hour": max(0, min(23, _ival("start_hour", 8))),
            "end_hour": max(1, min(24, _ival("end_hour", 21))),
            "min_interval_seconds": max(60, _ival("min_interval_seconds", 1200)),
            "max_interval_seconds": max(60, _ival("max_interval_seconds", 3000)),
        },
    }
    # Sanity: enforce min <= max for the random interval
    sched = new_cfg["schedule"]
    if sched["min_interval_seconds"] > sched["max_interval_seconds"]:
        sched["min_interval_seconds"], sched["max_interval_seconds"] = (
            sched["max_interval_seconds"], sched["min_interval_seconds"]
        )

    CONFIG_PATH.write_text(
        "# Edited via dashboard at " + time.strftime("%Y-%m-%d %H:%M:%S") + "\n"
        + yaml.safe_dump(new_cfg, sort_keys=False, allow_unicode=True)
    )
    _reload_config()
    return RedirectResponse("/config?saved=1", status_code=303)


# ── Guidelines editor ──────────────────────────────────────────────────

@app.get("/guidelines", response_class=HTMLResponse)
def guidelines_view(request: Request, saved: int = 0):
    body = GUIDELINES_PATH.read_text() if GUIDELINES_PATH.exists() else ""
    return templates.TemplateResponse(
        "guidelines.html",
        {"request": request, "body": body, "saved": bool(saved)},
    )


@app.post("/guidelines/save")
def guidelines_save(body: str = Form(...)):
    GUIDELINES_PATH.write_text(body)
    _reload_config()
    return RedirectResponse("/guidelines?saved=1", status_code=303)
