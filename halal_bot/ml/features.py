"""Point-in-time-safe feature engineering for the ML entry-signal spike
(SPEC of this experiment: see /Users/farisalmazrouei/.claude/plans/witty-juggling-mango.md).

Every feature here is derived only from OHLCV price/volume history
(halal_bot.data.prices) — never from fundamentals (halal_bot.data.fundamentals)
or TipRanks (halal_bot.research.tipranks_context), both of which only expose
a current/latest snapshot with no historical as-of API. Using either as a
training feature would silently reintroduce look-ahead bias, exactly the
failure mode halal_bot.research.tipranks_context and halal_bot.backtest.engine
already document for their own current-snapshot data. Reuses the same
indicator functions halal_bot.signals.strategy trades on, so a feature here
is never a second, subtly-different implementation of the same math.
"""
from __future__ import annotations

import pandas as pd

from halal_bot.signals.indicators import add_indicators, add_research_indicators, atr_pct
from halal_bot.config import CONFIG

FEATURE_COLUMNS = [
    "rsi", "sma_spread", "price_vs_sma_fast", "price_vs_sma_slow",
    "volume_ratio", "macd_hist", "macd_strength", "bb_percent", "adx",
    "atr_pct", "return_5d", "return_10d", "return_20d",
]


def build_feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """df: an OHLCV DataFrame (Open, High, Low, Close, Volume), same shape
    fetch_history() returns. Returns df with FEATURE_COLUMNS added — rows
    where any feature is still NaN (indicator warm-up window) are left in;
    callers doing training should dropna() themselves so this function stays
    reusable for live inference too, where a partial-warm-up row simply
    can't be scored yet."""
    cfg = CONFIG.risk
    out = add_indicators(df)
    out = add_research_indicators(out)

    out["sma_spread"] = out["sma_fast"] / out["sma_slow"] - 1
    out["price_vs_sma_fast"] = out["Close"] / out["sma_fast"] - 1
    out["price_vs_sma_slow"] = out["Close"] / out["sma_slow"] - 1
    out["volume_ratio"] = out["Volume"] / out["vol_avg"]
    out["macd_strength"] = out["macd_hist"] / out["Close"]
    out["atr_pct"] = atr_pct(out["High"], out["Low"], out["Close"], cfg.atr_period)
    out["return_5d"] = out["Close"].pct_change(5)
    out["return_10d"] = out["Close"].pct_change(10)
    out["return_20d"] = out["Close"].pct_change(20)

    return out
