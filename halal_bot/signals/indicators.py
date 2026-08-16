"""Technical indicators used by the rules-based signal engine.

No external TA library — these are small enough to implement directly and
it keeps the dependency footprint (and PythonAnywhere setup) minimal.
"""
from __future__ import annotations

import pandas as pd

from halal_bot.config import CONFIG


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def rsi(series: pd.Series, period: int) -> pd.Series:
    """Wilder's RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, float("nan"))
    result = 100 - (100 / (1 + rs))
    # When avg_loss is 0 (all gains), RSI is 100.
    result = result.where(avg_loss != 0, 100.0)
    return result


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Adds sma_fast, sma_slow, rsi, vol_avg columns to an OHLCV DataFrame.

    Expects columns: Open, High, Low, Close, Volume.
    """
    cfg = CONFIG.signal
    out = df.copy()
    out["sma_fast"] = sma(out["Close"], cfg.sma_fast)
    out["sma_slow"] = sma(out["Close"], cfg.sma_slow)
    out["rsi"] = rsi(out["Close"], cfg.rsi_period)
    out["vol_avg"] = out["Volume"].rolling(
        window=cfg.volume_lookback, min_periods=cfg.volume_lookback
    ).mean()
    return out
