#!/usr/bin/env python3
"""Saves a full backtest report (equity_curve.png, trades.csv, summary.json
under backtest_results/<timestamp>/) for the ML entry-signal spike
(halal_bot.ml -- BACKTESTED AND REJECTED, see scripts/train_ml_model.py and
halal_bot.signals.strategy's ml_filter docstring for the verdict), in the
same format scripts/run_backtest.py produces for the baseline rules-based
signal. Requires a trained model already saved via scripts/train_ml_model.py.

Usage:
    python scripts/backtest_ml_report.py [--skip-screening] [--no-cache]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from halal_bot.backtest.benchmark import simulate_dca_benchmark
from halal_bot.backtest.engine import BacktestEngine
from halal_bot.backtest.report import print_summary, write_report
from halal_bot.config import CONFIG
from halal_bot.data.prices import fetch_universe_history
from halal_bot.ml.model import load_model
from train_ml_model import ML_SCORE_THRESHOLD, build_compliant_universe


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-screening", action="store_true",
                         help="Skip AAOIFI screening, backtest the full watchlist as-is")
    parser.add_argument("--no-cache", action="store_true",
                         help="Force re-download of price history instead of using data/price_cache")
    args = parser.parse_args()

    if load_model() is None:
        print("No trained model found — run scripts/train_ml_model.py first.")
        return

    universe, sector_map = build_compliant_universe(args.skip_screening)
    if not universe:
        print("No compliant instruments — nothing to backtest.")
        return

    print(f"Fetching {CONFIG.backtest.lookback_years}y price history for {len(universe)} tickers...")
    price_data = fetch_universe_history(universe, CONFIG.backtest.lookback_years, use_cache=not args.no_cache)
    print(f"  Got price history for {len(price_data)}/{len(universe)} tickers.")

    caveats = [
        "ML entry signal (halal_bot.ml, ml_filter=True) — BACKTESTED AND REJECTED "
        "(held-out AUC 0.515, lost Sharpe/CAGR in every window: full/train/test). "
        "This report exists for the record, not as a live strategy candidate — "
        "halal_bot/live/daily_runner.py is untouched.",
        "Uses only price/volume-derived features — no fundamentals or TipRanks data "
        "(both are current-snapshot-only, unsafe for point-in-time backtesting; see "
        "halal_bot.ml.features module docstring).",
    ]

    print("\nRunning ML-signal backtest (full sample)...")
    engine = BacktestEngine(price_data, sector_map, ml_filter=True, ml_threshold=ML_SCORE_THRESHOLD)
    result = engine.run()

    print("Fetching S&P 500 (SPY) for benchmark comparison...")
    benchmark = simulate_dca_benchmark(
        result.equity_curve.index, CONFIG.backtest.starting_capital, 0.0,
        lookback_years=CONFIG.backtest.lookback_years,
    )
    if benchmark is None:
        caveats.append("SPY benchmark unavailable (no price history) -- comparison skipped.")

    print_summary(result, list(price_data.keys()), caveats, benchmark=benchmark)
    run_dir = write_report(result, list(price_data.keys()), caveats, benchmark=benchmark)
    print(f"\nFull report written to: {run_dir}")


if __name__ == "__main__":
    main()
