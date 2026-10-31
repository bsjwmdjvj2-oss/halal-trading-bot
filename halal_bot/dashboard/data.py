"""Loads the real (not backtested) trading history for the P&L dashboard
(SPEC.md Section 11) from the audit-trail log files."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from halal_bot.config import CONFIG


def load_equity_history() -> pd.Series:
    """One equity value per calendar day (last snapshot of that day if there
    were several — e.g. a manual re-run). Empty series if no live runs yet."""
    path = CONFIG.log_dir / "equity_history.jsonl"
    if not path.exists():
        return pd.Series(dtype=float)

    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    if not records:
        return pd.Series(dtype=float)

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("timestamp").drop_duplicates("date", keep="last")
    return df.set_index("date")["equity"].sort_index()


def total_pnl_line(current_equity: float) -> str | None:
    """One-line total P/L ($ and %) vs. the first logged equity day -- the same
    baseline /dashboard's "Total return" and the web dashboard use, so every
    surface reports one number. None when there's no history yet.

    Deposits/withdrawals aren't tracked (Alpaca's trading API exposes no
    transfer history), so cash added to the account would read as profit."""
    equity = load_equity_history()
    if equity.empty or float(equity.iloc[0]) <= 0:
        return None
    baseline = float(equity.iloc[0])
    pnl = current_equity - baseline
    return (
        f"📈 Total P/L: {'+' if pnl >= 0 else '-'}${abs(pnl):,.2f} "
        f"({pnl / baseline * 100:+.2f}%) since {equity.index[0]:%b %-d}"
    )


def load_all_trades() -> pd.DataFrame:
    """Concatenates every logs/trades_*.jsonl file. Empty DataFrame if none exist."""
    paths = sorted(CONFIG.log_dir.glob("trades_*.jsonl"))
    records = []
    for path in paths:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    if not records:
        return pd.DataFrame(columns=["date", "ticker", "action", "category", "shares",
                                      "price", "notional", "reason", "portfolio_equity", "pnl"])

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date")
