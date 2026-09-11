"""Minimal read-only Flask app exposing the same dashboard data
scripts/update_dashboard_snapshot.py commits to the repo (both call
halal_bot.live.dashboard_data.build_dashboard_snapshot()).

Deployed as a PythonAnywhere Web App (a separate resource from the existing
Always-on Task and Scheduled Task) -- its WSGI config file imports `app`
from this module.

NOTE on role: this was originally meant to be the TradeVant Snapshot
dashboard automation's data source, fetched directly by a scheduled cloud
routine. That routine's sandboxed network egress turned out to reject
arbitrary custom domains (org policy, verified by a real failed run) --
GitHub access worked fine, so the automation now reads a committed
data/dashboard_snapshot.json from the cloned repo instead (see
scripts/update_dashboard_snapshot.py). This endpoint is kept as a live,
on-demand way to check the same data manually (e.g. via curl) without
waiting for the next scheduled snapshot -- not load-bearing for the
automation itself anymore.

Read-only. No write/trading endpoint exists here or ever should -- this app
must never be able to place an order, only report on the account.
"""
from __future__ import annotations

import os

from flask import Flask, abort, jsonify, request

from halal_bot.broker.alpaca_client import AlpacaNotConfiguredError
from halal_bot.live.dashboard_data import build_dashboard_snapshot

app = Flask(__name__)

DASHBOARD_API_TOKEN = os.getenv("DASHBOARD_API_TOKEN", "")


@app.route("/dashboard-data")
def dashboard_data():
    if not DASHBOARD_API_TOKEN or request.args.get("token", "") != DASHBOARD_API_TOKEN:
        abort(403)

    try:
        return jsonify(build_dashboard_snapshot())
    except AlpacaNotConfiguredError as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    # Local smoke-test only -- PythonAnywhere's Web App config runs this
    # via a separate WSGI file, never this __main__ block.
    app.run(debug=False)
