"""Technical indicators used by the rules-based signal engine.

sma/rsi/volume are hand-rolled (small, no dependency needed) and are the
ones generate_signals() actually trades on — validated via backtesting and
an out-of-sample check (see conversation/commit history on
volume_confirm_multiplier). macd/bbands/adx use pandas-ta and are computed
for research only; wiring any of them into generate_signals() should go
through the same backtest-then-validate process before becoming live logic.
"""
from __future__ import annotations

import pandas as pd
import pandas_ta as ta

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


def _col(frame: pd.DataFrame, prefix: str) -> pd.Series:
    """First column starting with `prefix`. pandas-ta's exact suffix format
    (e.g. whether std shows up once or twice in a Bollinger Band column name)
    has changed across versions — matching by prefix is robust to that."""
    matches = [c for c in frame.columns if c.startswith(prefix)]
    if not matches:
        raise KeyError(f"No column starting with {prefix!r} in {list(frame.columns)}")
    return frame[matches[0]]


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """Average Directional Index — trend strength, 0-100. Shared by the
    research-only bundle below and by generate_signals()'s optional
    adx_filter (halal_bot.signals.strategy), so both use identical values."""
    result = ta.adx(high, low, close, length=period)
    return _col(result, "ADX_")


def macd(close: pd.Series, fast: int, slow: int, signal: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd_line, signal_line, histogram). Shared by the
    research-only bundle below and by generate_signals()'s optional
    macd_filter (halal_bot.signals.strategy), so both use identical values."""
    result = ta.macd(close, fast=fast, slow=slow, signal=signal)
    return _col(result, "MACD_"), _col(result, "MACDs_"), _col(result, "MACDh_")


def add_research_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Adds macd/macd_signal/macd_hist, bb_lower/bb_mid/bb_upper/bb_percent,
    and adx/plus_di/minus_di columns via pandas-ta. Research-only — nothing
    in generate_signals() reads these yet (see module docstring)."""
    cfg = CONFIG.signal
    out = df.copy()

    out["macd"], out["macd_signal"], out["macd_hist"] = macd(
        out["Close"], cfg.macd_fast, cfg.macd_slow, cfg.macd_signal
    )

    bbands = ta.bbands(out["Close"], length=cfg.bbands_period, std=cfg.bbands_std)
    out["bb_lower"] = _col(bbands, "BBL_")
    out["bb_mid"] = _col(bbands, "BBM_")
    out["bb_upper"] = _col(bbands, "BBU_")
    out["bb_percent"] = _col(bbands, "BBP_")

    adx_result = ta.adx(out["High"], out["Low"], out["Close"], length=cfg.adx_period)
    out["adx"] = _col(adx_result, "ADX_")
    out["plus_di"] = _col(adx_result, "DMP_")
    out["minus_di"] = _col(adx_result, "DMN_")

    return out


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Adds sma_fast, sma_slow, rsi, vol_avg columns to an OHLCV DataFrame.

    Expects columns: Open, High, Low, Close, Volume. This is what
    generate_signals() calls — deliberately does NOT include the research
    indicators above, so live/backtested behavior can't shift just by
    importing this module.
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
