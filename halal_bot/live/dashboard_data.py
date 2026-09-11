"""Shared data-gathering for the TradeVant Snapshot dashboard's automated
refresh -- used by both pythonanywhere_dashboard_app.py's read-only endpoint
and scripts/update_dashboard_snapshot.py's committed-file snapshot (see
/Users/farisalmazrouei/.claude/plans/witty-juggling-mango.md), so the same
"gather equity/cash/holdings/history/SPY" logic exists in exactly one place
rather than two copies drifting apart.
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

from halal_bot.broker.alpaca_client import AlpacaClient
from halal_bot.config import CONFIG


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def build_dashboard_snapshot() -> dict:
    """Raises AlpacaNotConfiguredError if Alpaca creds aren't set -- callers
    decide how to handle that (the Flask route returns a 500 JSON error; the
    snapshot script lets it propagate and fail the run visibly)."""
    account = AlpacaClient().get_account_snapshot()

    equity_history = _read_jsonl(CONFIG.log_dir / "equity_history.jsonl")
    trades = []
    for path in sorted(glob.glob(str(CONFIG.log_dir / "trades_*.jsonl"))):
        trades.extend(_read_jsonl(Path(path)))

    # Deferred import: keeps yfinance off the Flask app's import path until a
    # request actually needs it, same reasoning as AlpacaClient's own
    # deferred alpaca-py import.
    from halal_bot.data.prices import fetch_history

    spy = fetch_history("SPY", period_years=1)
    spy_history = (
        [{"date": str(d.date()), "close": round(float(c), 4)} for d, c in spy["Close"].items()]
        if not spy.empty else []
    )

    return {
        "equity": account.equity,
        "cash": account.cash,
        "holdings": account.positions,
        "equity_history": equity_history,
        "trades": trades,
        "spy_history": spy_history,
    }
