#!/usr/bin/env python3
"""Integration smoke test for the daily live-trading job, without real Alpaca
credentials: monkeypatches AlpacaClient with a fake account snapshot and
exercises the full dry-run pipeline (signal fetch, risk checks, order
skip-logging) against real market data.

Run: python tests/test_daily_runner.py
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import halal_bot.live.daily_runner as daily_runner
import halal_bot.live.state_store as state_store
from halal_bot.broker.alpaca_client import AccountSnapshot
from halal_bot.live.state_store import LiveState, save_state


class FakeAlpacaClient:
    """Stands in for a real Alpaca connection — one existing position, some cash."""

    def __init__(self):
        pass

    def get_account_snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(
            equity=20_000.0,
            cash=15_000.0,
            positions={
                "AAPL": {"qty": 10.0, "avg_entry_price": 150.0, "market_value": 2_500.0},
            },
        )

    def submit_market_order(self, ticker, qty, side):
        raise AssertionError(
            f"submit_market_order({ticker}, {qty}, {side}) called — dry run must never place orders"
        )

    def reconcile_state(self, expected):
        return []


def main() -> None:
    # Redirect state_store.STATE_PATH to a throwaway file for this run.
    # load_state()/save_state() both resolve STATE_PATH as a module global,
    # so reassigning it here covers every caller (this test, daily_runner)
    # without touching the real data/live_state.json. A prior version of
    # this test called save_state() with fake data before that redirect
    # existed, which overwrote the real production state file.
    with tempfile.TemporaryDirectory() as tmp_dir:
        state_store.STATE_PATH = Path(tmp_dir) / "test_live_state.json"

        today = datetime.now(timezone.utc).date().isoformat()
        save_state(LiveState(
            equity_peak=20_000.0,
            trading_paused=False,
            scaled_out_tickers=[],
            compliant_universe=["AAPL", "MSFT", "NVDA"],
            sector_map={"AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology"},
            last_screen_date=today,      # skip the (slow, network-heavy) re-screen this run
            last_rebalance_date=today,   # skip rebalance this run
        ))

        daily_runner.AlpacaClient = FakeAlpacaClient  # monkeypatch

        runner = daily_runner.DailyRunner(live=False)
        summary = runner.run_once()

    assert summary, "expected a non-empty summary"
    assert "Equity" in summary
    print("\n--- daily runner dry-run summary ---")
    print(summary)
    print("\nPASS: dry-run completed without submitting any orders")
    print("PASS: state I/O stayed isolated from the real data/live_state.json")


if __name__ == "__main__":
    main()
