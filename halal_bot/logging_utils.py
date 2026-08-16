"""Structured audit-trail logging (SPEC.md Section 10).

Every signal check and trade is written as one JSON line, so the full
reasoning behind a decision (not just the Telegram-visible summary) can be
reviewed before going live. One file per UTC day, append-only.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from halal_bot.config import CONFIG

_lock = threading.Lock()


def _log_path(prefix: str) -> Path:
    CONFIG.log_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return CONFIG.log_dir / f"{prefix}_{day}.jsonl"


def _write(prefix: str, record: dict[str, Any]) -> None:
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), **record}
    line = json.dumps(record, default=str)
    with _lock:
        with open(_log_path(prefix), "a") as f:
            f.write(line + "\n")


def log_signal(ticker: str, date: str, entry: bool, exit: bool, reason: str, indicators: dict) -> None:
    _write(
        "signals",
        {
            "ticker": ticker,
            "date": date,
            "entry_signal": entry,
            "exit_signal": exit,
            "reason": reason,
            "indicators": indicators,
        },
    )


def log_trade(
    ticker: str,
    date: str,
    action: str,
    shares: float,
    price: float,
    reason: str,
    portfolio_equity: float,
    category: str = "",
    pnl: float | None = None,
    dry_run: bool = False,
) -> None:
    _write(
        "trades",
        {
            "ticker": ticker,
            "date": date,
            "action": action,  # "buy" | "sell"
            "category": category,  # "buy" | "stop_loss" | "scale_out" | "signal_exit" | "rebalance_trim"
            "shares": shares,
            "price": price,
            "notional": shares * price,
            "reason": reason,
            "portfolio_equity": portfolio_equity,
            "pnl": pnl,  # realized P&L for sells; None for buys
            "dry_run": dry_run,  # True if this was logged but no real order was submitted
        },
    )


def log_equity_snapshot(date: str, equity: float, cash: float) -> None:
    """Appends to a single cumulative file (not day-partitioned like the rest)
    so the dashboard can read one continuous real equity curve over time."""
    CONFIG.log_dir.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), "date": date,
              "equity": equity, "cash": cash}
    with _lock:
        with open(CONFIG.log_dir / "equity_history.jsonl", "a") as f:
            f.write(json.dumps(record) + "\n")


def log_screening(ticker: str, compliant: bool, reasons: list[str], data_gaps: list[str]) -> None:
    _write(
        "screening",
        {
            "ticker": ticker,
            "compliant": compliant,
            "reasons": reasons,
            "data_gaps": data_gaps,
        },
    )


def log_event(category: str, message: str, **extra: Any) -> None:
    """Catch-all for things like drawdown-pause alerts, rebalance runs, restarts."""
    _write("events", {"category": category, "message": message, **extra})
