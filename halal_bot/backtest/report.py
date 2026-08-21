"""Writes backtest output: equity curve CSV, trade log CSV, summary JSON/text, PNG chart."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from halal_bot.backtest.engine import BacktestResult
from halal_bot.config import ROOT_DIR

RESULTS_DIR = ROOT_DIR / "backtest_results"


def write_report(result: BacktestResult, universe: list[str], data_caveats: list[str]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = RESULTS_DIR / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    result.equity_curve.to_csv(run_dir / "equity_curve.csv", header=True)

    trades_df = pd.DataFrame([asdict(t) for t in result.trades])
    trades_df.to_csv(run_dir / "trades.csv", index=False)

    summary = {
        "universe": universe,
        "num_trading_days": len(result.equity_curve),
        "metrics": asdict(result.metrics) if result.metrics else None,
        "total_contributed": result.total_contributed,
        "data_caveats": data_caveats,
    }
    with open(run_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    fig, ax = plt.subplots(figsize=(10, 5))
    result.equity_curve.plot(ax=ax)
    ax.set_title("Backtest Equity Curve")
    ax.set_ylabel("Portfolio Equity ($)")
    ax.set_xlabel("Date")
    fig.tight_layout()
    fig.savefig(run_dir / "equity_curve.png")
    plt.close(fig)

    return run_dir


def print_summary(result: BacktestResult, universe: list[str], data_caveats: list[str]) -> None:
    m = result.metrics
    print("\n" + "=" * 60)
    print("HALAL GROWTH BOT — BACKTEST SUMMARY")
    print("=" * 60)
    print(f"Universe ({len(universe)} tickers): {', '.join(universe)}")
    print(f"Trading days simulated: {len(result.equity_curve)}")
    if m:
        print(f"Total return:      {m.total_return_pct:+.2f}%")
        print(f"CAGR:              {m.cagr_pct:+.2f}%")
        print(f"Max drawdown:      {m.max_drawdown_pct:.2f}%")
        print(f"Sharpe-like ratio: {m.sharpe_ratio:.2f}")
        print(f"Win rate:          {m.win_rate_pct:.1f}% ({m.num_winning_trades}/{m.num_trades} closed trades)")
        if result.total_contributed:
            start_equity = result.equity_curve.iloc[0]
            end_equity = result.equity_curve.iloc[-1]
            net_trading_gain = end_equity - start_equity - result.total_contributed
            print(f"\n(Total return/CAGR above include deposited cash, not just trading gains)")
            print(f"Starting capital:  ${start_equity:,.2f}")
            print(f"Total contributed: ${result.total_contributed:,.2f}")
            print(f"Ending equity:     ${end_equity:,.2f}")
            print(f"Net trading gain/loss (ending equity - start - contributions): ${net_trading_gain:+,.2f}")
    else:
        print("No metrics computed (insufficient data).")
    print(f"Total trade log entries: {len(result.trades)}")
    if data_caveats:
        print("\nCaveats:")
        for c in data_caveats:
            print(f"  - {c}")
    print("=" * 60 + "\n")
