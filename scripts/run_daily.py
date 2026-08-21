#!/usr/bin/env python3
"""Entry point for PythonAnywhere's daily Scheduled Task.

Run once per trading day, after US market close (e.g. 21:15 UTC / 4:15pm ET).

Live trading is OFF by default (dry run — logs intended trades, submits
nothing). Set LIVE_TRADING_ENABLED=true in .env only once you've reviewed
several dry-run logs and are ready for the bot to place real (paper) orders.
"""
from __future__ import annotations

import asyncio
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from halal_bot.broker.alpaca_client import AlpacaNotConfiguredError
from halal_bot.live.daily_runner import run_once
from halal_bot.logging_utils import log_event


def _alert_failure(message: str) -> None:
    """Best-effort crash notification. This is the one failure path
    run_once() itself never reaches (it only sends its Telegram summary
    after a full successful run), so without this, a crash here is
    invisible outside of stderr and the local JSONL event log."""
    from halal_bot.telegram.bot import send_alert

    try:
        asyncio.run(send_alert(message))
    except Exception as e:
        print(f"(also failed to send Telegram alert: {e})", file=sys.stderr)


def main() -> int:
    live = os.getenv("LIVE_TRADING_ENABLED", "false").strip().lower() == "true"
    print(f"=== Daily run starting (live={live}) ===")
    try:
        # run_once() already prints every line in real time via its internal
        # _note() calls — its return value is that same log, joined into one
        # string for callers who want it programmatically (e.g. the test
        # harness). Printing it again here would just echo the run twice.
        run_once(live=live)
        print("=== Daily run completed ===")
        return 0
    except AlpacaNotConfiguredError as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        log_event("daily_run_config_error", str(e))
        _alert_failure(f"⚠️ CONFIG ERROR: daily run couldn't start — {e}")
        return 1
    except Exception as e:
        traceback.print_exc()
        log_event("daily_run_crashed", str(e))
        _alert_failure(f"🛑 DAILY RUN CRASHED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
