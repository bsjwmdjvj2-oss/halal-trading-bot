"""P&L dashboard (SPEC.md Section 11): daily/weekly/monthly P&L, win rate,
max drawdown, Sharpe-like ratio — computed from the bot's own real trading
history, not a backtest. Reuses halal_bot.backtest.metrics so "risk-adjusted,
not just raw P&L" means the same thing here as it did during backtesting.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from halal_bot.backtest.benchmark import simulate_dca_benchmark
from halal_bot.backtest.metrics import BacktestMetrics, compute_metrics
from halal_bot.config import ROOT_DIR
from halal_bot.dashboard.data import load_all_trades, load_equity_history

DASHBOARD_DIR = ROOT_DIR / "dashboard_results"

MIN_DAYS_FOR_RELIABLE_METRICS = 20  # roughly a month of daily runs


@dataclass
class Dashboard:
    equity_curve: pd.Series
    daily_pnl: pd.Series
    weekly_pnl: pd.Series
    monthly_pnl: pd.Series
    metrics: BacktestMetrics | None
    num_days: int
    reliable: bool  # False if too little history to trust CAGR/Sharpe
    spy_benchmark: pd.Series | None = None


def build_dashboard() -> Dashboard | None:
    equity = load_equity_history()
    if equity.empty:
        return None

    trades = load_all_trades()
    if "dry_run" in trades.columns:
        # Older log lines predate the dry_run field and default to False via
        # fillna — but any real deployment only ever gets this field going
        # forward, so this is just defensive, not a real backward-compat path.
        trades = trades[~trades["dry_run"].fillna(False)]
    closed_pnls = trades["pnl"].dropna().tolist() if "pnl" in trades.columns else []

    daily_pnl = equity.diff().dropna()
    weekly_pnl = equity.resample("W").last().diff().dropna()
    monthly_pnl = equity.resample("ME").last().diff().dropna()

    metrics = compute_metrics(equity, closed_pnls) if len(equity) > 1 else None

    # Lump-sum-since-first-day comparison only -- this log has no record of
    # *when* deposits happened, unlike the backtest's contribution events,
    # so it can't replay a matching DCA schedule the way
    # halal_bot.backtest.benchmark does for a backtest run. Flagged in
    # render_text() rather than pretending false precision.
    spy_benchmark = simulate_dca_benchmark(equity.index, float(equity.iloc[0]), monthly_contribution=0.0)

    return Dashboard(
        equity_curve=equity,
        daily_pnl=daily_pnl,
        weekly_pnl=weekly_pnl,
        monthly_pnl=monthly_pnl,
        metrics=metrics,
        num_days=len(equity),
        reliable=len(equity) >= MIN_DAYS_FOR_RELIABLE_METRICS,
        spy_benchmark=spy_benchmark,
    )


def render_text(dashboard: Dashboard | None) -> str:
    if dashboard is None:
        return ("📊 DASHBOARD\n\nNo equity history yet — this fills in after the first "
                "live daily run. Check back after a few days.")

    lines = ["📊 DASHBOARD", f"({dashboard.num_days} days of history)"]
    if not dashboard.reliable:
        lines.append(
            f"⚠️ Under {MIN_DAYS_FOR_RELIABLE_METRICS} days of history — CAGR/Sharpe below "
            f"are not yet meaningful, treat as provisional (see SPEC.md Section 13)."
        )
    lines.append("")

    m = dashboard.metrics
    if m:
        lines += [
            f"Total return:  {m.total_return_pct:+.2f}%",
            f"CAGR:          {m.cagr_pct:+.2f}%",
            f"Max drawdown:  {m.max_drawdown_pct:.2f}%",
            f"Sharpe-like:   {m.sharpe_ratio:.2f}",
            f"Win rate:      {m.win_rate_pct:.1f}% ({m.num_winning_trades}/{m.num_trades} closed trades)",
            "",
        ]

    if len(dashboard.daily_pnl):
        last = dashboard.daily_pnl.iloc[-1]
        lines.append(f"📅 Latest day P&L:  {last:+,.2f}")
    if len(dashboard.weekly_pnl):
        last = dashboard.weekly_pnl.iloc[-1]
        lines.append(f"🗓️ Latest week P&L: {last:+,.2f}")
    if len(dashboard.monthly_pnl):
        last = dashboard.monthly_pnl.iloc[-1]
        lines.append(f"📆 Latest month P&L: {last:+,.2f}")

    if dashboard.spy_benchmark is not None and len(dashboard.spy_benchmark):
        start_equity = float(dashboard.equity_curve.iloc[0])
        end_equity = float(dashboard.equity_curve.iloc[-1])
        spy_end = float(dashboard.spy_benchmark.iloc[-1])
        lines += [
            "",
            "📈 vs. S&P 500 (SPY), same starting equity:",
            f"  You:  ${end_equity:,.2f}  ({(end_equity/start_equity-1)*100:+.2f}%)",
            f"  SPY:  ${spy_end:,.2f}  ({(spy_end/start_equity-1)*100:+.2f}%)",
            "  (lump-sum comparison from day 1 -- doesn't account for deposits made along the way)",
        ]

    return "\n".join(lines)


def write_report(dashboard: Dashboard) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = DASHBOARD_DIR / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    dashboard.equity_curve.to_csv(run_dir / "equity_curve.csv", header=True)
    dashboard.daily_pnl.to_csv(run_dir / "daily_pnl.csv", header=["pnl"])
    dashboard.weekly_pnl.to_csv(run_dir / "weekly_pnl.csv", header=["pnl"])
    dashboard.monthly_pnl.to_csv(run_dir / "monthly_pnl.csv", header=["pnl"])
    if dashboard.spy_benchmark is not None:
        dashboard.spy_benchmark.to_csv(run_dir / "spy_benchmark.csv", header=True)

    with open(run_dir / "summary.json", "w") as f:
        json.dump({
            "num_days": dashboard.num_days,
            "reliable": dashboard.reliable,
            "metrics": asdict(dashboard.metrics) if dashboard.metrics else None,
            "spy_benchmark_ending_value": (
                float(dashboard.spy_benchmark.iloc[-1]) if dashboard.spy_benchmark is not None else None
            ),
        }, f, indent=2, default=str)

    fig, ax = plt.subplots(figsize=(10, 5))
    dashboard.equity_curve.plot(ax=ax, marker="o", label="Your account")
    if dashboard.spy_benchmark is not None:
        dashboard.spy_benchmark.plot(ax=ax, linestyle="--", label="S&P 500 (SPY)")
        ax.legend()
    ax.set_title("Live Equity Curve vs. S&P 500")
    ax.set_ylabel("Equity ($)")
    ax.set_xlabel("Date")
    fig.tight_layout()
    fig.savefig(run_dir / "equity_curve.png")
    plt.close(fig)

    return run_dir
