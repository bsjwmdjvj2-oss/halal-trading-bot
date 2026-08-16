"""Performance metrics for backtest results (SPEC.md Section 11)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252


@dataclass
class BacktestMetrics:
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    win_rate_pct: float
    num_trades: int
    num_winning_trades: int
    num_losing_trades: int


def max_drawdown(equity_curve: pd.Series) -> float:
    running_peak = equity_curve.cummax()
    drawdown = (equity_curve - running_peak) / running_peak
    return float(drawdown.min())


def sharpe_ratio(daily_returns: pd.Series, risk_free_rate: float = 0.0) -> float:
    excess = daily_returns - (risk_free_rate / TRADING_DAYS_PER_YEAR)
    std = excess.std()
    if std == 0 or np.isnan(std):
        return 0.0
    return float((excess.mean() / std) * np.sqrt(TRADING_DAYS_PER_YEAR))


def compute_metrics(equity_curve: pd.Series, closed_trade_pnls: list[float]) -> BacktestMetrics:
    daily_returns = equity_curve.pct_change().dropna()

    total_return = (equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1
    years = len(equity_curve) / TRADING_DAYS_PER_YEAR
    cagr = (equity_curve.iloc[-1] / equity_curve.iloc[0]) ** (1 / years) - 1 if years > 0 else 0.0

    winners = [p for p in closed_trade_pnls if p > 0]
    losers = [p for p in closed_trade_pnls if p <= 0]
    win_rate = (len(winners) / len(closed_trade_pnls) * 100) if closed_trade_pnls else 0.0

    return BacktestMetrics(
        total_return_pct=total_return * 100,
        cagr_pct=cagr * 100,
        max_drawdown_pct=max_drawdown(equity_curve) * 100,
        sharpe_ratio=sharpe_ratio(daily_returns),
        win_rate_pct=win_rate,
        num_trades=len(closed_trade_pnls),
        num_winning_trades=len(winners),
        num_losing_trades=len(losers),
    )
