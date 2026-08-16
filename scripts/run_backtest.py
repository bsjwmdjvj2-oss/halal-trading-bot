#!/usr/bin/env python3
"""End-to-end backtest runner: screen -> fetch history -> simulate -> report.

Usage:
    python scripts/run_backtest.py [--skip-screening] [--no-cache]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from halal_bot.config import CONFIG
from halal_bot.data.prices import fetch_universe_history
from halal_bot.logging_utils import log_screening
from halal_bot.screening.rules import screen_universe
from halal_bot.screening.watchlist import load_watchlist
from halal_bot.backtest.engine import BacktestEngine
from halal_bot.backtest.report import print_summary, write_report


def build_compliant_universe(skip_screening: bool) -> tuple[list[str], dict[str, str], list[str]]:
    instruments = load_watchlist()
    caveats = []

    if skip_screening:
        caveats.append("Screening skipped (--skip-screening) — using full watchlist unscreened.")
        sector_map = {i.ticker: i.sector for i in instruments}
        return [i.ticker for i in instruments], sector_map, caveats

    print(f"Screening {len(instruments)} instruments against AAOIFI-style rules "
          f"(live fundamentals lookup, this can take a minute)...")
    results = screen_universe(instruments)
    compliant, rejected = [], []
    for r in results:
        log_screening(r.ticker, r.compliant, r.reasons, r.data_gaps)
        (compliant if r.compliant else rejected).append(r)

    print(f"  Compliant: {len(compliant)}  |  Rejected: {len(rejected)}")
    for r in rejected:
        print(f"    REJECTED {r.ticker}: {'; '.join(r.reasons)}")

    gap_tickers = [r.ticker for r in compliant if r.data_gaps]
    if gap_tickers:
        caveats.append(
            f"{len(gap_tickers)} compliant tickers had missing fundamentals data for one or "
            f"more ratios (assumed passing on the missing ratio): {', '.join(gap_tickers)}"
        )
    caveats.append(
        "Screening uses CURRENT fundamentals applied across the whole backtest window — "
        "free data sources don't provide point-in-time historical fundamentals, so the "
        "monthly re-screen cadence (Section 3) is not retroactively simulated."
    )

    sector_map = {i.ticker: i.sector for i in instruments}
    compliant_tickers = [r.ticker for r in compliant]
    return compliant_tickers, sector_map, caveats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-screening", action="store_true",
                         help="Skip AAOIFI screening, backtest the full watchlist as-is")
    parser.add_argument("--no-cache", action="store_true",
                         help="Force re-download of price history instead of using data/price_cache")
    args = parser.parse_args()

    universe, sector_map, caveats = build_compliant_universe(args.skip_screening)
    if not universe:
        print("No compliant instruments — nothing to backtest.")
        return

    print(f"\nFetching {CONFIG.backtest.lookback_years}y price history for {len(universe)} tickers...")
    price_data = fetch_universe_history(
        universe, CONFIG.backtest.lookback_years, use_cache=not args.no_cache
    )
    missing = set(universe) - set(price_data)
    if missing:
        caveats.append(f"No price history available for: {', '.join(sorted(missing))}")
    print(f"  Got price history for {len(price_data)}/{len(universe)} tickers.")

    print("\nRunning backtest simulation...")
    engine = BacktestEngine(price_data, sector_map)
    result = engine.run()

    print_summary(result, list(price_data.keys()), caveats)
    run_dir = write_report(result, list(price_data.keys()), caveats)
    print(f"Full report written to: {run_dir}")


if __name__ == "__main__":
    main()
