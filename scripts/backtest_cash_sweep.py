#!/usr/bin/env python3
"""Backtests sweeping idle cash into SPSK (SP Funds Dow Jones Global Sukuk
ETF -- real, liquid, halal-compliant, low-volatility: traded in a
$17.51-$18.03 range over the past year) against leaving it at 0%. Same
full/train/test comparison convention as scripts/backtest_position_cap_sweep.py
(how the 20% position cap got adopted) and scripts/train_ml_model.py.

Read-only / backtest-only: halal_bot/live/daily_runner.py is untouched, and
SPSK is fetched separately from the stock-picking universe (never added to
data/watchlist.yaml) so it can't become a normal golden-cross/RSI entry
candidate -- see halal_bot.backtest.engine's cash_sweep docstring and
halal_bot.portfolio.PortfolioState.sweep_into_reserve/liquidate_reserve.

Usage:
    python scripts/backtest_cash_sweep.py [--skip-screening] [--no-cache]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from halal_bot.backtest.engine import BacktestEngine
from halal_bot.config import CONFIG
from halal_bot.data.prices import fetch_history, fetch_universe_history
from halal_bot.logging_utils import log_screening
from halal_bot.screening.rules import screen_universe
from halal_bot.screening.watchlist import load_watchlist

CASH_SWEEP_TICKER = "SPSK"


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


def print_row(name: str, baseline_val, swept_val) -> None:
    b = f"{baseline_val:.2f}" if isinstance(baseline_val, float) else str(baseline_val)
    s = f"{swept_val:.2f}" if isinstance(swept_val, float) else str(swept_val)
    print(f"  {name:<20} baseline={b:>10}   cash_sweep={s:>10}")


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

    print(f"Fetching {CASH_SWEEP_TICKER}'s own price history (kept separate from the "
          f"stock-picking universe)...")
    spsk_df = fetch_history(CASH_SWEEP_TICKER, CONFIG.backtest.lookback_years, use_cache=not args.no_cache)
    if spsk_df.empty:
        print(f"  No price history for {CASH_SWEEP_TICKER} — cannot run this sweep.")
        return
    print(f"  Got {len(spsk_df)} rows for {CASH_SWEEP_TICKER} "
          f"({spsk_df.index.min().date()} -> {spsk_df.index.max().date()}).")

    all_dates = sorted(set().union(*[df.index for df in price_data.values() if not df.empty]))
    split_idx = int(len(all_dates) * 2 / 3)  # 2/3 train, 1/3 test -- this repo's existing convention
    train_end = all_dates[split_idx]
    print(f"\nTrain window: {all_dates[0].date()} -> {train_end.date()}")
    print(f"Test window:  {all_dates[split_idx + 1].date()} -> {all_dates[-1].date()}")

    # macd_filter defaults True -- matches the live default exactly. Each
    # engine reused across all three windows (same "one engine, multiple
    # .run() calls" pattern as every other comparison script here) -- but
    # cash_sweep itself is fixed at construction (unlike max_position_size_pct,
    # it isn't read from CONFIG at run()-time), so unlike the position-cap
    # sweep this needs two separate engine instances, not one mutated between
    # calls.
    baseline_engine = BacktestEngine(price_data, sector_map)
    swept_engine = BacktestEngine(price_data, sector_map, cash_sweep=True,
                                   cash_sweep_ticker=CASH_SWEEP_TICKER, cash_sweep_price_data=spsk_df)

    print("\n" + "=" * 78)
    print(f"CASH SWEEP: idle cash left at 0% (baseline) vs swept into {CASH_SWEEP_TICKER}")
    print("=" * 78)

    for label, start, end in [
        ("FULL SAMPLE", None, None),
        ("TRAIN (in-sample)", None, train_end),
        ("TEST (held-out)", train_end, None),
    ]:
        baseline = baseline_engine.run(start_date=start, end_date=end)
        swept = swept_engine.run(start_date=start, end_date=end)
        bm, sm = baseline.metrics, swept.metrics
        print(f"\n--- {label} ---")
        print_row("Sharpe", bm.sharpe_ratio if bm else None, sm.sharpe_ratio if sm else None)
        print_row("CAGR %", bm.cagr_pct if bm else None, sm.cagr_pct if sm else None)
        print_row("Max drawdown %", bm.max_drawdown_pct if bm else None, sm.max_drawdown_pct if sm else None)
        print_row("Win rate %", bm.win_rate_pct if bm else None, sm.win_rate_pct if sm else None)
        print_row("Num trades", bm.num_trades if bm else None, sm.num_trades if sm else None)
        print(f"  {'Sweep round-trips':<20} baseline={'n/a':>10}   "
              f"cash_sweep={swept_engine.sweep_stats['events']:>10}")

    print("\nThis is a backtest-only comparison. Nothing here touches")
    print("halal_bot/live/daily_runner.py or real trading.")


if __name__ == "__main__":
    main()
