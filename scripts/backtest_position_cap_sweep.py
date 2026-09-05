#!/usr/bin/env python3
"""Backtests looser max_position_size_pct values against the current 15%
default. Motivated by a concrete finding in backtest_results/20260905_172652:
the monthly rebalance trim (which enforces this same cap) fired 62 times over
3 years, repeatedly cutting down the strategy's own best compounders (PANW
trimmed 6x, META/BIIB 4x each) -- a plausible mechanical reason the strategy
trails SPY's uncapped cap-weighted compounding. Same full/train/test
comparison convention as scripts/train_ml_model.py.

Read-only: CONFIG.risk.max_position_size_pct is restored before exit, and
this never touches halal_bot/live/daily_runner.py.

Usage:
    python scripts/backtest_position_cap_sweep.py [--skip-screening] [--no-cache]
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from halal_bot.backtest.engine import BacktestEngine
from halal_bot.config import CONFIG
from halal_bot.data.prices import fetch_universe_history
from halal_bot.logging_utils import log_screening
from halal_bot.screening.rules import screen_universe
from halal_bot.screening.watchlist import load_watchlist

CANDIDATE_PCTS = [0.15, 0.20, 0.25, 0.30]  # 0.15 = current live default


def build_compliant_universe(skip_screening: bool) -> tuple[list[str], dict[str, str]]:
    instruments = load_watchlist()
    sector_map = {i.ticker: i.sector for i in instruments}
    if skip_screening:
        return [i.ticker for i in instruments], sector_map
    print(f"Screening {len(instruments)} instruments against AAOIFI-style rules "
          f"(live fundamentals lookup, this can take a minute)...")
    results = screen_universe(instruments)
    compliant = []
    for r in results:
        log_screening(r.ticker, r.compliant, r.reasons, r.data_gaps)
        if r.compliant:
            compliant.append(r.ticker)
    print(f"  Compliant: {len(compliant)} / {len(instruments)}")
    return compliant, sector_map


def print_row(name: str, vals: list[tuple[float, str]]) -> None:
    print(f"  {name:<16} " + "  ".join(f"{p:.0%}={v:>9}" for p, v in vals))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-screening", action="store_true",
                         help="Skip AAOIFI screening, backtest the full watchlist as-is")
    parser.add_argument("--no-cache", action="store_true",
                         help="Force re-download of price history instead of using data/price_cache")
    args = parser.parse_args()

    universe, sector_map = build_compliant_universe(args.skip_screening)
    if not universe:
        print("No compliant instruments — nothing to backtest.")
        return

    print(f"\nFetching {CONFIG.backtest.lookback_years}y price history for {len(universe)} tickers...")
    price_data = fetch_universe_history(universe, CONFIG.backtest.lookback_years, use_cache=not args.no_cache)
    print(f"  Got price history for {len(price_data)}/{len(universe)} tickers.")

    all_dates = sorted(set().union(*[df.index for df in price_data.values() if not df.empty]))
    split_idx = int(len(all_dates) * 2 / 3)  # 2/3 train, 1/3 test -- this repo's existing convention
    train_end = all_dates[split_idx]
    print(f"\nTrain window: {all_dates[0].date()} -> {train_end.date()}")
    print(f"Test window:  {all_dates[split_idx + 1].date()} -> {all_dates[-1].date()}")

    original_risk = CONFIG.risk
    # macd_filter defaults True -- matches the live default exactly. Position
    # cap isn't read until run(), so one engine instance is reused across
    # every pct/window combination (same pattern as the train/test split
    # itself: one engine, multiple .run() calls).
    engine = BacktestEngine(price_data, sector_map)

    print("\n" + "=" * 78)
    print("POSITION-SIZE CAP SWEEP: max_position_size_pct 15% (current) vs looser caps")
    print("=" * 78)

    try:
        for label, start, end in [
            ("FULL SAMPLE", None, None),
            ("TRAIN (in-sample)", None, train_end),
            ("TEST (held-out)", train_end, None),
        ]:
            print(f"\n--- {label} ---")
            rows: dict[str, list[tuple[float, str]]] = {
                "Sharpe": [], "CAGR %": [], "Max drawdown %": [],
                "Win rate %": [], "Num trades": [], "Trim events": [],
            }
            for pct in CANDIDATE_PCTS:
                object.__setattr__(CONFIG, "risk", dataclasses.replace(original_risk, max_position_size_pct=pct))
                result = engine.run(start_date=start, end_date=end)
                m = result.metrics
                trims = sum(1 for t in result.trades if t.action == "rebalance_trim")
                rows["Sharpe"].append((pct, f"{m.sharpe_ratio:.2f}" if m else "n/a"))
                rows["CAGR %"].append((pct, f"{m.cagr_pct:.1f}" if m else "n/a"))
                rows["Max drawdown %"].append((pct, f"{m.max_drawdown_pct:.1f}" if m else "n/a"))
                rows["Win rate %"].append((pct, f"{m.win_rate_pct:.1f}" if m else "n/a"))
                rows["Num trades"].append((pct, str(m.num_trades) if m else "n/a"))
                rows["Trim events"].append((pct, str(trims)))
            for name, vals in rows.items():
                print_row(name, vals)
    finally:
        object.__setattr__(CONFIG, "risk", original_risk)

    print("\nCONFIG.risk.max_position_size_pct restored to its original value -- this was")
    print("a read-only sweep. Nothing here touches halal_bot/live/daily_runner.py or real trading.")


if __name__ == "__main__":
    main()
