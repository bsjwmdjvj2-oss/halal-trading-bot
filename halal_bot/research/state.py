"""Cadence tracking for the quarterly research agent — deliberately separate
from halal_bot.live.state_store, since this has no dependency on Alpaca or
the live trading loop at all (pure research, run standalone)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date

from halal_bot.config import ROOT_DIR

STATE_PATH = ROOT_DIR / "data" / "research_state.json"


@dataclass
class ResearchState:
    last_review_date: str | None = None
    last_tipranks_refresh_date: str | None = None


def load_state() -> ResearchState:
    if not STATE_PATH.exists():
        return ResearchState()
    with open(STATE_PATH) as f:
        return ResearchState(**json.load(f))


def save_state(state: ResearchState) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(asdict(state), f, indent=2)


def days_since(date_str: str | None, today: date) -> int:
    if date_str is None:
        return 10**6  # never run before -> always due
    return (today - date.fromisoformat(date_str)).days
