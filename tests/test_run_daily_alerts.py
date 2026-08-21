#!/usr/bin/env python3
"""Smoke test for scripts/run_daily.py's crash-path Telegram alerting: a
daily run that raises before reaching daily_runner's own end-of-run summary
must still notify, not fail silently on the PythonAnywhere box. Stubs
send_alert so no real Telegram call is made.

Run: python tests/test_run_daily_alerts.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("run_daily", ROOT / "scripts" / "run_daily.py")
run_daily = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_daily)

import halal_bot.telegram.bot as bot
from halal_bot.broker.alpaca_client import AlpacaNotConfiguredError


def _stub_alerts() -> list[str]:
    sent: list[str] = []

    async def fake_send_alert(message: str) -> None:
        sent.append(message)

    bot.send_alert = fake_send_alert
    return sent


def test_crash_sends_alert() -> None:
    sent = _stub_alerts()

    def boom(live: bool) -> str:
        raise ValueError("simulated crash mid-run")

    run_daily.run_once = boom
    code = run_daily.main()

    assert code == 1, f"expected exit code 1, got {code}"
    assert len(sent) == 1, f"expected exactly one alert, got {sent}"
    assert "DAILY RUN CRASHED" in sent[0]
    assert "simulated crash mid-run" in sent[0]
    print("PASS: uncaught exception in run_once() still sends a Telegram alert")


def test_config_error_sends_alert() -> None:
    sent = _stub_alerts()

    def unconfigured(live: bool) -> str:
        raise AlpacaNotConfiguredError("ALPACA_API_KEY not set")

    run_daily.run_once = unconfigured
    code = run_daily.main()

    assert code == 1, f"expected exit code 1, got {code}"
    assert len(sent) == 1, f"expected exactly one alert, got {sent}"
    assert "CONFIG ERROR" in sent[0]
    print("PASS: AlpacaNotConfiguredError still sends a Telegram alert")


def test_alert_send_failure_does_not_crash_the_handler() -> None:
    async def broken_send_alert(message: str) -> None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    bot.send_alert = broken_send_alert

    def boom(live: bool) -> str:
        raise ValueError("simulated crash mid-run")

    run_daily.run_once = boom
    code = run_daily.main()  # must not raise even though the alert send itself fails

    assert code == 1, f"expected exit code 1, got {code}"
    print("PASS: a failed alert send doesn't take down the crash handler itself")


if __name__ == "__main__":
    test_crash_sends_alert()
    test_config_error_sends_alert()
    test_alert_send_failure_does_not_crash_the_handler()
