#!/usr/bin/env python3
"""Trains halal_bot.ml's entry-signal classifier and backtests it head-to-head
against the existing rules-based signal (halal_bot.signals.strategy), using
this repo's existing full/train/test comparison convention.

BACKTEST-ONLY RESEARCH SPIKE: this does not touch halal_bot/live/daily_runner.py
or real trading. See /Users/farisalmazrouei/.claude/plans/witty-juggling-mango.md
for the design and why fundamentals/TipRanks data can't safely be ML features.

Usage:
    python scripts/train_ml_model.py [--skip-screening] [--no-cache]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from sklearn.metrics import precision_score, recall_score, roc_auc_score

from halal_bot.backtest.engine import BacktestEngine
from halal_bot.config import CONFIG
from halal_bot.data.prices import fetch_universe_history
from halal_bot.logging_utils import log_screening
from halal_bot.ml.features import FEATURE_COLUMNS, build_feature_matrix
from halal_bot.ml.labels import build_labels, drop_unlabelable_tail
from halal_bot.ml.model import ModelMeta, save_model, train_model
from halal_bot.screening.rules import screen_universe
from halal_bot.screening.watchlist import load_watchlist

HORIZON_DAYS = 10
THRESHOLD_PCT = 0.0    # forward return > 0% -> positive label
ML_SCORE_THRESHOLD = 0.5


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


def build_labeled_table(
    price_data: dict[str, pd.DataFrame], start: pd.Timestamp | None, end: pd.Timestamp | None,
    apply_leak_guard: bool = False,
) -> tuple[pd.DataFrame, pd.Series]:
    """Feature+label rows for every ticker, restricted to (start, end].
    apply_leak_guard=True additionally drops each ticker's last HORIZON_DAYS
    rows (halal_bot.ml.labels.drop_unlabelable_tail) so no label peeks past
    end into the held-out window -- pass this for the TRAIN table only; the
    TEST table isn't fit on, so its labels are safe to use whole."""
    rows, labels = [], []
    for ticker, df in price_data.items():
        if df.empty:
            continue
        feats = build_feature_matrix(df)
        combined = feats[FEATURE_COLUMNS].copy()
        combined["label"] = build_labels(feats, horizon_days=HORIZON_DAYS, threshold_pct=THRESHOLD_PCT)
        if start is not None:
            combined = combined[combined.index > start]
        if end is not None:
            combined = combined[combined.index <= end]
        if apply_leak_guard:
            combined = drop_unlabelable_tail(combined, HORIZON_DAYS)
        combined = combined.dropna()
        if combined.empty:
            continue
        rows.append(combined[FEATURE_COLUMNS])
        labels.append(combined["label"].astype(bool))
    if not rows:
        return pd.DataFrame(columns=FEATURE_COLUMNS), pd.Series(dtype=bool)
    return pd.concat(rows), pd.concat(labels)


def print_comparison_row(name: str, baseline_val, ml_val) -> None:
    b = f"{baseline_val:.2f}" if isinstance(baseline_val, float) else str(baseline_val)
    m = f"{ml_val:.2f}" if isinstance(ml_val, float) else str(ml_val)
    print(f"  {name:<16} baseline={b:>10}   ml={m:>10}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-screening", action="store_true",
                         help="Skip AAOIFI screening, train/backtest on the full watchlist as-is")
    parser.add_argument("--no-cache", action="store_true",
                         help="Force re-download of price history instead of using data/price_cache")
    args = parser.parse_args()

    universe, sector_map = build_compliant_universe(args.skip_screening)
    if not universe:
        print("No compliant instruments — nothing to train on.")
        return

    print(f"\nFetching {CONFIG.backtest.lookback_years}y price history for {len(universe)} tickers...")
    price_data = fetch_universe_history(universe, CONFIG.backtest.lookback_years, use_cache=not args.no_cache)
    print(f"  Got price history for {len(price_data)}/{len(universe)} tickers.")

    all_dates = sorted(set().union(*[df.index for df in price_data.values() if not df.empty]))
    split_idx = int(len(all_dates) * 2 / 3)  # 2/3 train, 1/3 test -- this repo's existing convention
    train_end = all_dates[split_idx]
    print(f"\nTrain window: {all_dates[0].date()} -> {train_end.date()}")
    print(f"Test window:  {all_dates[split_idx + 1].date()} -> {all_dates[-1].date()}")

    print("\nBuilding training table...")
    X_train, y_train = build_labeled_table(price_data, start=None, end=train_end, apply_leak_guard=True)
    if X_train.empty:
        print("No labeled training rows — aborting.")
        return
    print(f"  {len(X_train)} labeled rows, {y_train.mean():.1%} positive "
          f"(forward {HORIZON_DAYS}d return > {THRESHOLD_PCT:.0%})")

    print("Training HistGradientBoostingClassifier...")
    model = train_model(X_train, y_train)

    print("\nEvaluating on held-out test window...")
    X_test, y_test = build_labeled_table(price_data, start=train_end, end=None)
    test_auc = test_precision = test_recall = None
    if not X_test.empty:
        test_scores = model.predict_proba(X_test[FEATURE_COLUMNS])[:, 1]
        test_pred = test_scores > ML_SCORE_THRESHOLD
        test_auc = roc_auc_score(y_test, test_scores)
        test_precision = precision_score(y_test, test_pred, zero_division=0)
        test_recall = recall_score(y_test, test_pred, zero_division=0)
        print(f"  AUC: {test_auc:.3f}  Precision: {test_precision:.3f}  Recall: {test_recall:.3f}  "
              f"(rows: {len(X_test)}, positive rate: {y_test.mean():.1%})")
        if test_auc < 0.55:
            print("  NOTE: AUC this close to 0.5 (coin flip) suggests no real predictive edge — "
                  "treat the backtest comparison below with real skepticism regardless of its result.")
    else:
        print("  No test-window rows available — skipping classification metrics.")

    print("\nSaving model to data/ml_model.joblib / data/ml_model_meta.json...")
    save_model(model, ModelMeta(
        trained_at=pd.Timestamp.now(tz="UTC").isoformat(),
        train_start=str(all_dates[0].date()),
        train_end=str(train_end.date()),
        feature_columns=FEATURE_COLUMNS,
        horizon_days=HORIZON_DAYS,
        threshold_pct=THRESHOLD_PCT,
        train_rows=len(X_train),
        test_auc=test_auc,
        test_precision=test_precision,
        test_recall=test_recall,
    ))

    print("\n" + "=" * 70)
    print("BACKTEST COMPARISON: ML entry signal (full replacement) vs. baseline rules")
    print("=" * 70)
    # Same engine instance reused across windows via repeated .run() calls --
    # the established pattern in this codebase for train/test comparisons
    # (see halal_bot/backtest/engine.py's own docstring / commit history).
    baseline_engine = BacktestEngine(price_data, sector_map)
    ml_engine = BacktestEngine(price_data, sector_map, ml_filter=True, ml_threshold=ML_SCORE_THRESHOLD)
    for label, start, end in [
        ("FULL SAMPLE", None, None),
        ("TRAIN (in-sample)", None, train_end),
        ("TEST (held-out)", train_end, None),
    ]:
        baseline = baseline_engine.run(start_date=start, end_date=end)
        ml = ml_engine.run(start_date=start, end_date=end)
        print(f"\n--- {label} ---")
        bm, mm = baseline.metrics, ml.metrics
        print_comparison_row("Sharpe", bm.sharpe_ratio if bm else None, mm.sharpe_ratio if mm else None)
        print_comparison_row("CAGR %", bm.cagr_pct if bm else None, mm.cagr_pct if mm else None)
        print_comparison_row("Max drawdown %", bm.max_drawdown_pct if bm else None, mm.max_drawdown_pct if mm else None)
        print_comparison_row("Win rate %", bm.win_rate_pct if bm else None, mm.win_rate_pct if mm else None)
        print_comparison_row("Num trades", bm.num_trades if bm else None, mm.num_trades if mm else None)

    print("\nThis ML signal is NOT wired into live trading (halal_bot.live.daily_runner")
    print("is untouched). If these numbers look genuinely better across ALL THREE")
    print("windows — not just one — that's worth discussing; going live is a separate,")
    print("explicit decision, same bar this codebase already holds every other")
    print("backtested feature to.")


if __name__ == "__main__":
    main()
