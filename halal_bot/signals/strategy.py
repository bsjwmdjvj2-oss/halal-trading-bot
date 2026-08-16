"""Rules-based entry/exit signal logic (SPEC.md Section 4).

Entry (all must hold):
  - Golden cross: sma_fast crosses above sma_slow (trend turning up)
  - RSI not overbought: rsi < rsi_overbought (avoid chasing exhausted moves)
  - Volume confirmation: volume > volume_confirm_multiplier * 20d avg volume

Exit (signal-triggered, independent of stop-loss/profit-take in halal_bot.risk):
  - Death cross: sma_fast crosses below sma_slow (trend turning down)

Thresholds live in halal_bot.config.SignalConfig and are tuned via backtesting.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from halal_bot.config import CONFIG
from halal_bot.signals.indicators import add_indicators


@dataclass
class SignalRow:
    date: pd.Timestamp
    entry: bool
    exit: bool
    reasons: list[str]


def generate_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Returns df with indicators + entry_signal, exit_signal, signal_reason columns."""
    cfg = CONFIG.signal
    out = add_indicators(df)

    prev_fast = out["sma_fast"].shift(1)
    prev_slow = out["sma_slow"].shift(1)

    golden_cross = (out["sma_fast"] > out["sma_slow"]) & (prev_fast <= prev_slow)
    death_cross = (out["sma_fast"] < out["sma_slow"]) & (prev_fast >= prev_slow)

    rsi_ok_for_entry = out["rsi"] < cfg.rsi_overbought
    volume_confirmed = out["Volume"] > (cfg.volume_confirm_multiplier * out["vol_avg"])

    out["entry_signal"] = golden_cross & rsi_ok_for_entry & volume_confirmed
    out["exit_signal"] = death_cross

    def _reason(row) -> str:
        parts = []
        if row["entry_signal"]:
            parts.append(
                f"golden cross (sma{cfg.sma_fast}={row['sma_fast']:.2f} > "
                f"sma{cfg.sma_slow}={row['sma_slow']:.2f}), "
                f"rsi={row['rsi']:.1f} < {cfg.rsi_overbought}, "
                f"volume={row['Volume']:.0f} > "
                f"{cfg.volume_confirm_multiplier}x avg({row['vol_avg']:.0f})"
            )
        if row["exit_signal"]:
            parts.append(
                f"death cross (sma{cfg.sma_fast}={row['sma_fast']:.2f} < "
                f"sma{cfg.sma_slow}={row['sma_slow']:.2f})"
            )
        return "; ".join(parts)

    out["signal_reason"] = out.apply(_reason, axis=1)
    return out
