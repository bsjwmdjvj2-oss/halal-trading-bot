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


def _reconcile_with_alpaca_history(trades: list[dict], client: AlpacaClient) -> list[dict]:
    """The self-logged trades above only cover orders this bot itself
    submitted (log_trade() is only ever called from daily_runner's own
    _submit_and_log()). A trade placed manually outside the bot (e.g.
    directly in the Alpaca app) never gets logged there, so the dashboard
    would silently keep showing a stale position count/holdings list
    forever -- exactly what happened when MA/V were sold manually on
    2026-09-08 and the dashboard kept reporting 6 open positions.

    Fetches Alpaca's own real fill history since the earliest locally-logged
    trade date and adds any fill not already represented locally (matched by
    ticker+action+share count, since a locally-logged order's *date* can
    differ from its actual fill date/price for an after-hours submission
    that fills at the next session's open -- the share count is the only
    field guaranteed to match exactly). Tagged category="manual" so the UI
    can distinguish it from a bot-initiated trade.
    """
    if not trades:
        return trades

    known = {
        (t["ticker"], t["action"], round(float(t["shares"]), 6)) for t in trades
    }
    earliest = min(t["date"] for t in trades)

    for o in client.get_filled_orders(after=earliest):
        key = (o["ticker"], o["action"], round(o["shares"], 6))
        if key in known:
            continue
        trades.append(
            {
                "ticker": o["ticker"],
                "date": o["date"],
                "action": o["action"],
                "category": "manual",
                "shares": o["shares"],
                "price": o["price"],
                "notional": o["shares"] * o["price"],
                "reason": "Placed outside the bot (e.g. Alpaca app) -- filled order, no local log entry",
                "portfolio_equity": None,
                "pnl": None,
                "dry_run": False,
                "timestamp": o["timestamp"],
            }
        )
        known.add(key)

    trades.sort(key=lambda t: t["timestamp"])
    return trades


def build_dashboard_snapshot() -> dict:
    """Raises AlpacaNotConfiguredError if Alpaca creds aren't set -- callers
    decide how to handle that (the Flask route returns a 500 JSON error; the
    snapshot script lets it propagate and fail the run visibly)."""
    client = AlpacaClient()
    account = client.get_account_snapshot()

    equity_history = _read_jsonl(CONFIG.log_dir / "equity_history.jsonl")
    trades = []
    for path in sorted(glob.glob(str(CONFIG.log_dir / "trades_*.jsonl"))):
        trades.extend(_read_jsonl(Path(path)))
    trades = _reconcile_with_alpaca_history(trades, client)

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
