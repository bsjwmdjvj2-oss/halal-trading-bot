#!/usr/bin/env python3
"""Entry point for PythonAnywhere's Always-on Task.

Runs the Telegram bot's polling loop indefinitely so /status, /pause, and
/resume are responsive at any time (not just once a day like the trading
job). Requires the paid Hacker-tier-or-above plan — the free tier can't run
always-on tasks or reach api.telegram.org reliably.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from halal_bot.telegram.bot import run_bot

if __name__ == "__main__":
    print("Starting Telegram bot polling loop...")
    run_bot()
