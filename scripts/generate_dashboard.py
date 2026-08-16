#!/usr/bin/env python3
"""Generate a P&L dashboard (SPEC.md Section 11) from the bot's real trading
history (logs/equity_history.jsonl + logs/trades_*.jsonl) — not a backtest.

Usage:
    python scripts/generate_dashboard.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from halal_bot.dashboard.report import build_dashboard, render_text, write_report


def main() -> None:
    dashboard = build_dashboard()
    print(render_text(dashboard))
    if dashboard is not None:
        run_dir = write_report(dashboard)
        print(f"\nFull report written to: {run_dir}")


if __name__ == "__main__":
    main()
