"""TipRanks market-data context for the quarterly research agent.

TipRanks is only reachable here via an MCP connection that exists in a
Claude Code chat session — it has no separate API-key/REST access this
codebase can call directly, unlike Alpaca/Telegram/Anthropic. So this is a
snapshot-file pattern, not a live client: whoever has that MCP connection
(a human at the keyboard, or Claude Code in a session) periodically calls
save_snapshot() with freshly-pulled TipRanks data; run_quarterly_review()
then picks it up via load_snapshot() if one exists and isn't stale, and
degrades to no TipRanks context if not — same "quietly do without it"
philosophy as the AI summary agent missing its API key.

This intentionally does the quantitative halal screen itself (reusing
halal_bot.screening.rules.screen_instrument, the same live numeric check
the bot already trusts) rather than asking the research LLM to guess at
compliance — it only has to do qualitative research on names that already
passed the real ratio screen.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from halal_bot.config import ROOT_DIR
from halal_bot.screening.watchlist import Instrument

SNAPSHOT_PATH = ROOT_DIR / "data" / "tipranks_snapshot.json"

# Best-effort sector labels for ratio-screening a ticker we don't have a
# watchlist entry for yet -- only used to feed screen_instrument(), which
# itself only reads market/financial data; the sector exclusion check still
# runs against whatever TipRanks reports for that ticker too.
_SECTOR_FALLBACK = "Unknown"


@dataclass
class TipRanksList:
    name: str
    tickers: list[dict] = field(default_factory=list)  # raw rows, must include "ticker"


def save_snapshot(lists: list[TipRanksList], path: Path = SNAPSHOT_PATH) -> None:
    """Called with freshly-pulled TipRanks data (see module docstring) --
    not something this codebase can call unattended."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "lists": [{"name": lst.name, "tickers": lst.tickers} for lst in lists],
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def load_snapshot(path: Path = SNAPSHOT_PATH, max_age_days: int = 35) -> dict | None:
    """None if no snapshot exists or it's older than max_age_days (default
    ~35, comfortably covering the ~90-day quarterly cadence's own tolerance
    for "close enough" while still refusing to present visibly stale data
    as current)."""
    if not path.exists():
        return None
    with open(path) as f:
        payload = json.load(f)
    fetched_at = datetime.fromisoformat(payload["fetched_at"])
    age_days = (datetime.now(timezone.utc) - fetched_at).days
    if age_days > max_age_days:
        return None
    return payload


def format_context(snapshot: dict, watchlist: list[Instrument], halal_client=None) -> str:
    """Cross-references every ticker across all TipRanks lists against the
    current watchlist, runs the real halal ratio screen on whichever ones
    aren't already tracked, and returns a compact markdown block ready to
    splice into the research prompt. Returns "" if nothing new survived.

    halal_client (optional, halal_bot.screening.halal_terminal_client.
    HalalTerminalClient): when given, cross-checks the tickers that passed
    the internal ratio screen against Halal Terminal's 5-methodology
    verdict too -- capped to the top 8 to stay well inside a free-tier
    monthly quota (500 tokens, ~5-10 per screen call) even run every
    quarter. A ticker that disagrees between the two screens is flagged,
    not silently resolved either way -- that's exactly the kind of thing
    that should reach the human reviewer, not get decided automatically.
    """
    from halal_bot.screening.rules import screen_instrument

    known_tickers = {i.ticker for i in watchlist}
    seen: dict[str, dict] = {}
    for lst in snapshot["lists"]:
        for row in lst["tickers"]:
            ticker = row.get("ticker", "").upper()
            if not ticker or ticker in known_tickers or ticker in seen:
                continue
            seen[ticker] = row

    if not seen:
        return ""

    passed: list[str] = []
    failed: list[str] = []
    for ticker, row in seen.items():
        sector = row.get("sectorName") or row.get("sector") or _SECTOR_FALLBACK
        try:
            result = screen_instrument(Instrument(ticker=ticker, sector=sector, is_etf=False))
        except Exception:
            continue  # no usable fundamentals data at all -- skip rather than guess
        label = f"{ticker} ({row.get('companyName') or row.get('company', '')})".strip()
        if result.compliant:
            passed.append(label)
        else:
            failed.append(f"{label} — {'; '.join(result.reasons)}")

    fetched_at = snapshot["fetched_at"][:10]
    lines = [f"TipRanks market data (pulled {fetched_at}), cross-referenced against your "
             f"current watchlist and pre-screened through the same quantitative ratio check:"]
    if passed:
        lines.append(
            "\nAlready passed the ratio screen, not yet on the watchlist — TipRanks-favored "
            "(top Smart Score / top analyst-rated / trending) candidates worth researching first:"
        )
        lines.append("  " + ", ".join(sorted(passed)))
    if failed:
        lines.append("\nTipRanks-favored but already failed the ratio screen (skip, don't re-research):")
        lines.append("  " + "; ".join(sorted(failed)))

    if halal_client is not None and passed:
        passed_tickers = [label.split(" (", 1)[0] for label in sorted(passed)][:8]
        verdicts = halal_client.screen_batch(passed_tickers)
        if verdicts:
            lines.append(
                "\nHalal Terminal cross-check (5-methodology professional screen, independent "
                "of the ratio screen above) on those same candidates:"
            )
            for ticker in passed_tickers:
                v = verdicts.get(ticker)
                if v is None:
                    lines.append(f"  {ticker}: no Halal Terminal data returned")
                    continue
                status = "COMPLIANT" if v.compliant else "NON-COMPLIANT"
                agree = "agrees" if v.compliant else "DISAGREES — flag for review"
                methods = ", ".join(
                    f"{m}={'Y' if ok else 'N'}" for m, ok in v.methodology_summary.items()
                )
                lines.append(f"  {ticker}: {status} ({agree}) — {methods}")
            disclaimer = next((v.disclaimer for v in verdicts.values() if v.disclaimer), "")
            if disclaimer:
                lines.append(f"\n({disclaimer})")

    return "\n".join(lines)
