#!/usr/bin/env python3
"""Writes data/dashboard_snapshot.json (equity/cash/holdings/history/SPY --
see halal_bot.live.dashboard_data.build_dashboard_snapshot) and commits +
pushes ONLY that one file to the repo.

Why a committed file instead of a live endpoint: the scheduled cloud
routine that refreshes the TradeVant Snapshot dashboard artifact runs in a
sandbox whose network egress is restricted by an organization policy --
verified by a real failed run, arbitrary custom domains (including this
project's own PythonAnywhere Web App) get rejected. GitHub access through
that same sandbox works fine (it already clones this repo), so committing
the data here instead lets the routine just read it from its own already-
cloned checkout, no network call needed.

Called from scripts/run_daily.py after a live run, best-effort (a snapshot/
push failure must never be treated as a trading-logic failure -- see that
script's try/except around this call).

Requires push-capable git credentials in this checkout (PythonAnywhere's
existing clone typically only has pull/fetch access, since it's only ever
needed `git pull` before now) -- see this script's own error message if
push fails for exactly what to set up.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from halal_bot.config import ROOT_DIR
from halal_bot.live.dashboard_data import build_dashboard_snapshot

SNAPSHOT_PATH = ROOT_DIR / "data" / "dashboard_snapshot.json"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT_DIR, capture_output=True, text=True)


def main() -> int:
    snapshot = build_dashboard_snapshot()
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SNAPSHOT_PATH, "w") as f:
        json.dump(snapshot, f, indent=2, default=str)
    print(f"Wrote {SNAPSHOT_PATH} "
          f"(equity=${snapshot['equity']:,.2f}, {len(snapshot['holdings'])} holdings, "
          f"{len(snapshot['equity_history'])} equity points, {len(snapshot['trades'])} trades)")

    # Narrowly scoped: only ever stages this one file, never `git add -A` --
    # a reporting script must not be able to accidentally commit anything
    # else lying around in the working tree.
    add = _run("git", "add", str(SNAPSHOT_PATH))
    if add.returncode != 0:
        print(f"git add failed: {add.stderr}", file=sys.stderr)
        return 1

    diff = _run("git", "diff", "--cached", "--quiet")
    if diff.returncode == 0:
        print("No change since last snapshot -- nothing to commit.")
        return 0

    commit = _run("git", "commit", "-m", "Update dashboard snapshot [automated]")
    if commit.returncode != 0:
        print(f"git commit failed: {commit.stderr}", file=sys.stderr)
        return 1

    push = _run("git", "push")
    if push.returncode != 0:
        print(
            "git push failed -- this checkout likely only has pull/fetch access.\n"
            "Set up a push-capable credential, e.g. a fine-grained GitHub Personal\n"
            "Access Token scoped to just this repo (Contents: read and write), then:\n"
            "  git remote set-url origin "
            "https://<github-username>:<token>@github.com/bsjwmdjvj2-oss/halal-trading-bot.git\n"
            f"Underlying error: {push.stderr}",
            file=sys.stderr,
        )
        return 1

    print("Pushed dashboard snapshot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
