# Facebook Marketplace auto-reply

Polls your FB Marketplace inbox, drafts replies with Claude based on
guidelines + per-listing context, and either auto-sends or queues for review.

## ⚠️ Read first

- This automates Facebook in a way that **violates Meta's Terms of Service**.
  Use a dedicated account for the listings, not your personal one.
- The DOM selectors in `src/poller.py` and `src/sender.py` will break when
  Facebook changes their UI. Run `python -m src.main inspect` to debug.
- AI replies can be wrong. Start in `draft` mode. Move to `hybrid` only after
  you trust the drafts on a representative sample.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env             # add ANTHROPIC_API_KEY
cp config.yaml.example config.yaml
cp listings.yaml.example listings.yaml
cp guidelines.md.example guidelines.md
# edit all three
```

## First run

1. **Log in** (one time; cookies persist in `data/browser_profile/`):
   ```bash
   python -m src.main login
   ```
   Log in manually, complete 2FA, then Ctrl+C.

2. **Tune selectors** if the inbox layout has shifted:
   ```bash
   python -m src.main inspect
   ```

3. **One cycle, draft mode** (set `send_mode: draft` in `config.yaml`):
   ```bash
   python -m src.main run --once
   python -m src.main review
   ```

4. **Continuous, hybrid mode** once you trust it:
   ```bash
   # set send_mode: hybrid in config.yaml
   python -m src.main run
   ```

5. **Dashboard** for reviewing drafts in a browser:
   ```bash
   python -m src.main dashboard
   # opens http://127.0.0.1:8765/
   ```

## Send modes

- `draft` — every reply queued, nothing auto-sent. Review with `review`.
- `auto` — every reply auto-sent. **Don't use for v1.**
- `hybrid` — auto-send unless the inbound or the draft contains an
  `escalation_triggers` keyword (price, tour scheduling, legal). Anything that
  matches is queued for review.

## Files

- `data/state.db` — SQLite. Threads, messages, drafts.
- `data/browser_profile/` — Playwright user data dir. Holds your FB session.
- `guidelines.md` — system prompt for Claude. Tune this; it's most of the leverage.
- `listings.yaml` — per-listing facts Claude can quote from.
