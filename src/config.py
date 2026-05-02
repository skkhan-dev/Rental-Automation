from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PROFILE_DIR = DATA_DIR / "browser_profile"
DB_PATH = DATA_DIR / "state.db"


@dataclass
class Config:
    poll_interval_seconds: int
    send_mode: str  # auto | draft | hybrid
    cycle_cap: int
    escalation_triggers: list[str]
    delay_between_replies_seconds: list[int]
    typing_delay_ms: list[int]
    model: str
    guidelines: str
    listings: list[dict]


def load() -> Config:
    cfg_path = ROOT / "config.yaml"
    listings_path = ROOT / "listings.yaml"
    guidelines_path = ROOT / "guidelines.md"

    for p in (cfg_path, listings_path, guidelines_path):
        if not p.exists():
            raise FileNotFoundError(
                f"Missing {p.name}. Copy {p.name}.example to {p.name} and edit."
            )

    cfg = yaml.safe_load(cfg_path.read_text())
    listings_doc = yaml.safe_load(listings_path.read_text())
    guidelines = guidelines_path.read_text()

    DATA_DIR.mkdir(exist_ok=True)
    PROFILE_DIR.mkdir(exist_ok=True)

    return Config(
        poll_interval_seconds=cfg["poll_interval_seconds"],
        send_mode=cfg["send_mode"],
        cycle_cap=int(cfg.get("cycle_cap", 5)),
        escalation_triggers=[t.lower() for t in cfg["escalation_triggers"]],
        delay_between_replies_seconds=cfg["delay_between_replies_seconds"],
        typing_delay_ms=cfg["typing_delay_ms"],
        model=cfg["model"],
        guidelines=guidelines,
        listings=listings_doc["listings"],
    )
