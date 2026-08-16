"""Persistent state shared between the daily runner and the always-on Telegram
bot process (SPEC.md Section 10 — graceful recovery).

Alpaca itself is the source of truth for cash/positions/qty/avg_entry_price,
so this store only holds the handful of things Alpaca doesn't track:
equity high-water mark (for the drawdown pause), the manual pause flag (set
via /pause, needs to survive across two separate OS processes), which
positions have already fired their scale-out, and bookkeeping for the
monthly re-screen/rebalance cadence.

A plain JSON file is enough here — single bot, single account, low write
frequency (a few times a day at most), and PythonAnywhere gives a normal
persistent filesystem.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from halal_bot.config import ROOT_DIR

STATE_PATH = ROOT_DIR / "data" / "live_state.json"

_lock = threading.Lock()


@dataclass
class LiveState:
    equity_peak: float = 0.0
    previous_equity: float = 0.0  # last run's ending equity, for period-over-period % in the AI summary
    trading_paused: bool = False
    scaled_out_tickers: list[str] = field(default_factory=list)
    compliant_universe: list[str] = field(default_factory=list)
    sector_map: dict[str, str] = field(default_factory=dict)
    last_screen_date: str | None = None
    last_rebalance_date: str | None = None


def load_state() -> LiveState:
    with _lock:
        if not STATE_PATH.exists():
            return LiveState()
        with open(STATE_PATH) as f:
            raw = json.load(f)
        return LiveState(**raw)


def save_state(state: LiveState) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        tmp_path = STATE_PATH.with_suffix(".json.tmp")
        with open(tmp_path, "w") as f:
            json.dump(asdict(state), f, indent=2)
        tmp_path.replace(STATE_PATH)  # atomic on POSIX — avoids a torn read mid-write


def set_paused(paused: bool) -> None:
    state = load_state()
    state.trading_paused = paused
    save_state(state)
