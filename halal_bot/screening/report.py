"""On-demand view of the most recent halal screening results (SPEC.md
Section 3), read from the audit-trail log rather than re-run live —
screening 127 tickers against live fundamentals takes minutes, far too
slow for a synchronous Telegram reply (and would stall the bot's polling
loop for everything else in the meantime).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from halal_bot.config import CONFIG


@dataclass
class ScreeningSnapshot:
    screened_on: str  # date, from the log filename
    compliant: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (ticker, reason)


def load_latest_screening() -> ScreeningSnapshot | None:
    paths = sorted(CONFIG.log_dir.glob("screening_*.jsonl"))
    if not paths:
        return None
    latest = paths[-1]
    screened_on = latest.stem.removeprefix("screening_")

    by_ticker: dict[str, dict] = {}
    with open(latest) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            by_ticker[record["ticker"]] = record  # last entry wins if duplicated

    compliant = sorted(t for t, r in by_ticker.items() if r["compliant"])
    rejected = sorted(
        (t, "; ".join(r["reasons"])) for t, r in by_ticker.items() if not r["compliant"]
    )
    return ScreeningSnapshot(screened_on=screened_on, compliant=compliant, rejected=rejected)


def render_text(snapshot: ScreeningSnapshot | None) -> str:
    if snapshot is None:
        return ("🕌 HALAL SCREENING\n\nNo screening data yet — runs on the first live run, "
                "then monthly.")

    total = len(snapshot.compliant) + len(snapshot.rejected)
    lines = [
        "🕌 HALAL SCREENING",
        f"(from re-screen on {snapshot.screened_on})",
        "",
        f"✅ Compliant: {len(snapshot.compliant)}/{total}",
    ]

    if snapshot.rejected:
        lines.append("")
        lines.append(f"❌ Rejected: {len(snapshot.rejected)}")
        for ticker, reason in snapshot.rejected:
            lines.append(f"  {ticker}: {reason}")

    if snapshot.compliant:
        lines.append("")
        lines.append("📋 Compliant tickers:")
        lines.append(", ".join(snapshot.compliant))

    return "\n".join(lines)
