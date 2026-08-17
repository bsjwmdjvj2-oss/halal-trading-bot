#!/usr/bin/env python3
"""Entry point for a PythonAnywhere Scheduled Task.

Runs daily (or weekly), but only actually does anything roughly every
CONFIG.portfolio.strategy_review_interval_days (90 days / quarterly) --
PythonAnywhere's own scheduler doesn't support a "quarterly" cadence
directly, so this script checks internally and no-ops most days, same
pattern halal_bot.live.daily_runner already uses for the monthly
re-screen/rebalance checks.

Advisory only: writes a report, sends it to Telegram, never touches
watchlist.yaml. A human decides whether to act on anything in it.
"""
from __future__ import annotations

import asyncio
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from halal_bot.config import CONFIG, ROOT_DIR
from halal_bot.logging_utils import log_event
from halal_bot.research.quarterly_review import run_quarterly_review
from halal_bot.research.state import days_since, load_state, save_state

RESULTS_DIR = ROOT_DIR / "research_results"


def _write_report(report: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"quarterly_review_{stamp}.md"
    path.write_text(report)
    return path


async def _send_to_telegram(report: str) -> None:
    from halal_bot.telegram.bot import send_alert

    header = "🔎 QUARTERLY RESEARCH REPORT\n\n"
    text = header + report
    # Telegram's ~4096-char message cap -- same chunking approach as /screening.
    for i in range(0, len(text), 4000):
        await send_alert(text[i:i + 4000])


def main() -> int:
    today = datetime.now(timezone.utc).date()
    state = load_state()

    if days_since(state.last_review_date, today) < CONFIG.portfolio.strategy_review_interval_days:
        print(f"Not due yet (last review: {state.last_review_date or 'never'}, "
              f"interval: {CONFIG.portfolio.strategy_review_interval_days} days). Skipping.")
        return 0

    print("Quarterly review due -- running research (this makes real web searches, "
          "can take a few minutes)...")
    try:
        report = run_quarterly_review()
    except Exception as e:
        traceback.print_exc()
        log_event("quarterly_research_failed", str(e))
        return 1

    path = _write_report(report)
    print(f"Report written to: {path}")
    print(report)

    state.last_review_date = today.isoformat()
    save_state(state)
    log_event("quarterly_research_completed", "Report generated", path=str(path))

    try:
        asyncio.run(_send_to_telegram(report))
    except RuntimeError as e:
        print(f"Telegram send failed (report is still saved to disk): {e}")
        log_event("quarterly_research_telegram_failed", str(e))

    return 0


if __name__ == "__main__":
    sys.exit(main())
