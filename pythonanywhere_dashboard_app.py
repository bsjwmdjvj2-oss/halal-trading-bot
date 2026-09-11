"""Minimal read-only Flask app for the TradeVant Snapshot dashboard's
automated refresh (see /Users/farisalmazrouei/.claude/plans/witty-juggling-mango.md).

Deployed as a PythonAnywhere Web App (a separate resource from the existing
Always-on Task and Scheduled Task) -- its WSGI config file imports `app`
from this module. A single authenticated route serves exactly what the
Portfolio P&L panel needs: live equity/cash/holdings (from AlpacaClient,
the same client every other live code path in this repo uses) plus the
on-disk equity-history and trade logs (halal_bot.logging_utils' own output
files) -- so a scheduled cloud routine can fetch one JSON blob instead of a
human running a one-liner and pasting the result, as happened manually
several times this session.

Read-only. No write/trading endpoint exists here or ever should -- this app
must never be able to place an order, only report on the account.
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path

from flask import Flask, abort, jsonify, request

from halal_bot.broker.alpaca_client import AlpacaClient, AlpacaNotConfiguredError
from halal_bot.config import CONFIG

app = Flask(__name__)

DASHBOARD_API_TOKEN = os.getenv("DASHBOARD_API_TOKEN", "")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


@app.route("/dashboard-data")
def dashboard_data():
    if not DASHBOARD_API_TOKEN or request.args.get("token", "") != DASHBOARD_API_TOKEN:
        abort(403)

    try:
        account = AlpacaClient().get_account_snapshot()
    except AlpacaNotConfiguredError as e:
        return jsonify({"error": str(e)}), 500

    equity_history = _read_jsonl(CONFIG.log_dir / "equity_history.jsonl")
    trades = []
    for path in sorted(glob.glob(str(CONFIG.log_dir / "trades_*.jsonl"))):
        trades.extend(_read_jsonl(Path(path)))

    return jsonify({
        "equity": account.equity,
        "cash": account.cash,
        "holdings": account.positions,
        "equity_history": equity_history,
        "trades": trades,
    })


if __name__ == "__main__":
    # Local smoke-test only -- PythonAnywhere's Web App config runs this
    # via a separate WSGI file, never this __main__ block.
    app.run(debug=False)
