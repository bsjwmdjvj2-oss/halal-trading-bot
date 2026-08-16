"""Rules-based entry/exit signal logic (SPEC.md Section 4).

Entry (all must hold):
  - Golden cross: sma_fast crosses above sma_slow (trend turning up)
  - RSI not overbought: rsi < rsi_overbought (avoid chasing exhausted moves)
  - Volume confirmation: volume > volume_confirm_multiplier * 20d avg volume
  - [optional, off by default] ADX trend filter: adx > adx_trend_threshold —
    require the market to actually be trending, not choppy/sideways, since
    a golden cross in a ranging market is the classic whipsaw failure mode
    for this kind of system. NOT live — see adx_filter param below.

Exit (signal-triggered, independent of stop-loss/profit-take in halal_bot.risk):
  - Death cross: sma_fast crosses below sma_slow (trend turning down)

Thresholds live in halal_bot.config.SignalConfig and are tuned via backtesting.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from halal_bot.config import CONFIG
from halal_bot.signals.indicators import add_indicators, adx as compute_adx


@dataclass
class SignalRow:
    date: pd.Timestamp
    entry: bool
    exit: bool
    reasons: list[str]


def generate_signals(df: pd.DataFrame, adx_filter: bool = False) -> pd.DataFrame:
    """Returns df with indicators + entry_signal, exit_signal, signal_reason columns.

    adx_filter: opt-in, defaults to False so every existing caller (the live
    daily runner, the backtester's default run) is completely unaffected.
    Exists so the ADX variant can be backtested and out-of-sample validated
    against the baseline before ever becoming the default — see conversation/
    commit history on volume_confirm_multiplier for why that step matters.
    """
    cfg = CONFIG.signal
    out = add_indicators(df)

    prev_fast = out["sma_fast"].shift(1)
    prev_slow = out["sma_slow"].shift(1)

    golden_cross = (out["sma_fast"] > out["sma_slow"]) & (prev_fast <= prev_slow)
    death_cross = (out["sma_fast"] < out["sma_slow"]) & (prev_fast >= prev_slow)

    rsi_ok_for_entry = out["rsi"] < cfg.rsi_overbought
    volume_confirmed = out["Volume"] > (cfg.volume_confirm_multiplier * out["vol_avg"])

    entry_condition = golden_cross & rsi_ok_for_entry & volume_confirmed

    if adx_filter:
        out["adx"] = compute_adx(out["High"], out["Low"], out["Close"], cfg.adx_period)
        entry_condition = entry_condition & (out["adx"] > cfg.adx_trend_threshold)

    out["entry_signal"] = entry_condition
    out["exit_signal"] = death_cross

    def _reason(row) -> str:
        parts = []
        if row["entry_signal"]:
            reason = (
                f"golden cross (sma{cfg.sma_fast}={row['sma_fast']:.2f} > "
                f"sma{cfg.sma_slow}={row['sma_slow']:.2f}), "
                f"rsi={row['rsi']:.1f} < {cfg.rsi_overbought}, "
                f"volume={row['Volume']:.0f} > "
                f"{cfg.volume_confirm_multiplier}x avg({row['vol_avg']:.0f})"
            )
            if adx_filter:
                reason += f", adx={row['adx']:.1f} > {cfg.adx_trend_threshold}"
            parts.append(reason)
        if row["exit_signal"]:
            parts.append(
                f"death cross (sma{cfg.sma_fast}={row['sma_fast']:.2f} < "
                f"sma{cfg.sma_slow}={row['sma_slow']:.2f})"
            )
        return "; ".join(parts)

    out["signal_reason"] = out.apply(_reason, axis=1)
    return out
