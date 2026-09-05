"""S&P 500 (SPY) benchmark comparison for backtest and live-dashboard reports.

Answers "how would the same money have done in a plain index fund instead" --
a strategy is only actually adding value if it beats this, not just if it's
"up" in absolute terms.
"""
from __future__ import annotations

import pandas as pd

from halal_bot.backtest.engine import REBALANCE_INTERVAL_TRADING_DAYS
from halal_bot.data.prices import fetch_history

BENCHMARK_TICKER = "SPY"


def simulate_dca_benchmark(
    dates: pd.DatetimeIndex,
    starting_capital: float,
    monthly_contribution: float = 0.0,
    lookback_years: int = 3,
) -> pd.Series | None:
    """SPY buy-and-hold using the exact same starting capital + contribution
    cadence (every REBALANCE_INTERVAL_TRADING_DAYS trading days, matching
    halal_bot.backtest.engine's own contribution timing) as the strategy run
    it's being compared against -- an apples-to-apples DCA-vs-DCA comparison,
    not a lump-sum SPY comparison that would understate SPY's own
    DCA-smoothed risk the same way a lump-sum backtest would overstate a
    DCA'd strategy's risk if compared to a lump-sum SPY curve.

    Returns None if SPY price history isn't available for this window.
    """
    spy = fetch_history(BENCHMARK_TICKER, period_years=lookback_years, use_cache=True)
    if spy.empty:
        return None
    spy_ff = spy["Close"].reindex(dates).ffill()
    if spy_ff.isna().all():
        return None

    shares = 0.0
    values = []
    invested = False
    for i, price in enumerate(spy_ff):
        if pd.isna(price):
            values.append(float("nan"))
            continue
        # starting_capital goes in on the first day SPY actually HAS a price,
        # not rigidly at position 0 -- if the strategy's own equity curve
        # starts before SPY's fetched history does (real scenario: per-ticker
        # price_cache files written on different days end up with slightly
        # different "N years back" windows, see halal_bot.data.prices), day 0
        # here is NaN and the old i==0 check silently skipped the entire
        # investment forever, zeroing the whole benchmark.
        if not invested:
            contribution = starting_capital
            invested = True
        elif monthly_contribution and i % REBALANCE_INTERVAL_TRADING_DAYS == 0:
            contribution = monthly_contribution
        else:
            contribution = 0.0
        if contribution and price > 0:
            shares += contribution / price
        values.append(shares * price)

    return pd.Series(values, index=dates, name="spy_benchmark").ffill()
