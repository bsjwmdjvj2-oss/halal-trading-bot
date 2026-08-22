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


def save_snapshot(
    lists: list[TipRanksList],
    path: Path = SNAPSHOT_PATH,
    news_articles: list[dict] | None = None,
    market_commentary: dict | None = None,
    consensus_ratings: dict[str, dict] | None = None,
    ai_analysis_scores: dict[str, dict] | None = None,
) -> None:
    """Called with freshly-pulled TipRanks data (see module docstring) --
    not something this codebase can call unattended. Overwrites the whole
    file, so a caller updating just one part (e.g. only refreshing news)
    must pass the other parts through too (e.g. via load_snapshot() first)
    or they're dropped.

    news_articles (optional): raw rows from get_assets_news/get_latest_news
    -- each expected to carry at least ticker, title, sentiment, url, date.
    market_commentary (optional): the raw dict from get_market_commentary
    (overallSentiment, atmosphere, keyThemes, tailwinds, headwinds) --
    explicitly AI-generated per that tool's own docs, surfaced as such.
    consensus_ratings (optional): {ticker: {"consensus": str, "price_target":
    float|None, "upside_pct": float|None}} from get_assets_data, one entry
    per watchlist ticker -- unlike top_smart_score/top_rated (TipRanks' own
    curated top-N lists), this is meant to cover the WHOLE watchlist so
    analyst_buy_tickers() below can fail closed on anything not present.
    ai_analysis_scores (optional): {ticker: {"ai_score": float, "rating":
    str}} from get_ai_stock_analysis, also meant to cover the whole
    watchlist (batched -- that tool caps at 25 tickers/call). This is
    TipRanks' separate cross-model AI score, not the Smart Score."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "lists": [{"name": lst.name, "tickers": lst.tickers} for lst in lists],
        "news_articles": news_articles or [],
        "market_commentary": market_commentary,
        "consensus_ratings": consensus_ratings or {},
        "ai_analysis_scores": ai_analysis_scores or {},
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


def smart_score_10_tickers(snapshot: dict | None) -> set[str]:
    """Tickers currently rated a perfect TipRanks Smart Score (10/10).

    Deliberately LIVE-ONLY -- there is no historical point-in-time Smart
    Score data available to us (TipRanks exposes only the current score),
    so unlike every other opt-in experiment in this codebase
    (adx_filter/vol_sizing/trailing_stop/rank_entries/min_hold_days), this
    cannot be backtested without look-ahead bias: a stock's current score
    is partly a function of its own recent price performance, so "did
    today's top scorers do well historically" is close to circular, not a
    real test of predictive power. Used only as a same-day entry-collision
    priority in halal_bot.live.daily_runner (never a backtest input) --
    tracked forward from when it's deployed, not retroactively validated."""
    if snapshot is None:
        return set()
    for lst in snapshot["lists"]:
        if lst["name"] == "top_smart_score":
            return {r.get("ticker", "").upper() for r in lst["tickers"] if r.get("smartScore") == 10}
    return set()


CONSENSUS_STALE_DAYS = 14  # analyst consensus moves slower than news but not glacially


def analyst_buy_tickers(snapshot: dict | None) -> set[str]:
    """Tickers currently carrying a TipRanks "Buy" or "Strong Buy" analyst
    consensus, from consensus_ratings (see save_snapshot). Widened from
    Strong-Buy-only: 78/139 watchlist tickers were Strong Buy alone vs.
    132/139 for Buy-or-better -- Strong-Buy-only was excluding names like
    AAPL (rated plain "Buy") that are unremarkable exclusions, not real
    quality signals. Fails closed: a ticker missing from consensus_ratings
    (never fetched, or the snapshot is stale) is NOT treated as eligible,
    same principle as halal_bot.screening.rules failing a ticker closed on
    missing fundamentals rather than defaulting it to compliant.

    Used as a HARD GATE on new entries in halal_bot.live.daily_runner, on
    top of the existing technical entry_signal -- a ticker needs both to
    be bought. Same live-only caveat as smart_score_10_tickers: analyst
    consensus can't be backtested without look-ahead bias (a rating
    reflects the stock's own recent performance), so this is tracked
    forward from deployment, not retroactively validated."""
    if snapshot is None:
        return set()
    fetched_at = datetime.fromisoformat(snapshot["fetched_at"])
    if (datetime.now(timezone.utc) - fetched_at).days > CONSENSUS_STALE_DAYS:
        return set()
    ratings = snapshot.get("consensus_ratings") or {}
    return {t.upper() for t, r in ratings.items() if r.get("consensus") in ("StrongBuy", "Buy")}


def ai_score_map(snapshot: dict | None) -> dict[str, float]:
    """{ticker: 0-100 TipRanks AI Stock Analysis score} from
    ai_analysis_scores (see save_snapshot) -- the cross-model AI score
    (OpenAI/Anthropic/Gemini/xAI/DeepSeek/Perplexity), NOT the same as
    Smart Score. Empty dict (not fail-closed to block anything) if missing
    or stale -- this is a ranking input, not a gate, so "no data" just
    means that ticker sorts with the alphabetical fallback rather than
    blocking it the way analyst_buy_tickers does."""
    if snapshot is None:
        return {}
    fetched_at = datetime.fromisoformat(snapshot["fetched_at"])
    if (datetime.now(timezone.utc) - fetched_at).days > CONSENSUS_STALE_DAYS:
        return {}
    scores = snapshot.get("ai_analysis_scores") or {}
    return {t.upper(): r["ai_score"] for t, r in scores.items() if r.get("ai_score") is not None}


def price_target_upside_map(snapshot: dict | None) -> dict[str, float]:
    """{ticker: analyst avg 12-month price-target upside, decimal (0.05 =
    +5%)} from consensus_ratings -- same source/staleness/priority-not-gate
    treatment as ai_score_map."""
    if snapshot is None:
        return {}
    fetched_at = datetime.fromisoformat(snapshot["fetched_at"])
    if (datetime.now(timezone.utc) - fetched_at).days > CONSENSUS_STALE_DAYS:
        return {}
    ratings = snapshot.get("consensus_ratings") or {}
    return {t.upper(): r["upside_pct"] for t, r in ratings.items() if r.get("upside_pct") is not None}


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
                if v.methodology_summary:
                    methods = ", ".join(
                        f"{m}={'Y' if ok else 'N'}" for m, ok in v.methodology_summary.items()
                    )
                else:
                    # No per-methodology breakdown at all -- Halal Terminal
                    # short-circuits ratio screening when the business
                    # activity screen itself already fails.
                    methods = "failed business-activity screen (no ratio breakdown run)"
                lines.append(f"  {ticker}: {status} ({agree}) — {methods}")
            disclaimer = next((v.disclaimer for v in verdicts.values() if v.disclaimer), "")
            if disclaimer:
                lines.append(f"\n({disclaimer})")

    return "\n".join(lines)


def _classify(rows: list[dict], known_tickers: set[str]) -> tuple[list[str], list[str], list[str]]:
    """One TipRanks list (e.g. top_rated) -> (already-tracked tickers,
    new-and-halal-pass tickers, new-and-halal-fail tickers). Dedupes by
    ticker within the list."""
    from halal_bot.screening.rules import screen_instrument

    tracked: list[str] = []
    new_pass: list[str] = []
    new_fail: list[str] = []
    seen: set[str] = set()
    for row in rows:
        ticker = row.get("ticker", "").upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        if ticker in known_tickers:
            tracked.append(ticker)
            continue
        sector = row.get("sectorName") or row.get("sector") or _SECTOR_FALLBACK
        try:
            result = screen_instrument(Instrument(ticker=ticker, sector=sector, is_etf=False))
        except Exception:
            continue  # no usable fundamentals data -- skip rather than guess
        (new_pass if result.compliant else new_fail).append(ticker)
    return tracked, new_pass, new_fail


def format_telegram_screening(snapshot: dict, watchlist: list[Instrument]) -> str:
    """Compact, chat-ready summary for the /screening Telegram command:
    latest top analyst-rated (Strong Buy consensus), Smart Score == 10, and
    AI Stock Analysis score > 80 -- each list cross-referenced against the
    watchlist, with anything not yet tracked run through the real halal
    ratio screen. Deliberately terse (counts + ticker lists, not a
    paragraph per stock) to fit a phone screen. Returns "" if the snapshot
    has none of these three lists."""
    known = {i.ticker for i in watchlist}
    by_name = {lst["name"]: lst["tickers"] for lst in snapshot["lists"]}
    fetched_at = snapshot["fetched_at"][:10]

    sections = [
        ("⭐ Top analyst-rated", by_name.get("top_rated", [])),
        ("🏆 Smart Score 10", [r for r in by_name.get("top_smart_score", []) if r.get("smartScore") == 10]),
        ("🤖 AI score > 80", [r for r in by_name.get("ai_scores", []) if (r.get("ai_score") or 0) > 80]),
    ]
    if not any(rows for _, rows in sections):
        return ""

    lines = [f"📊 TipRanks screen (as of {fetched_at})"]
    for label, rows in sections:
        if not rows:
            continue
        tracked, new_pass, new_fail = _classify(rows, known)
        lines.append(f"\n{label} ({len(rows)} total, {len(tracked)} already tracked):")
        lines.append(f"  New + halal-pass: {', '.join(sorted(new_pass)) or '(none)'}")

    return "\n".join(lines)


NEWS_STALE_DAYS = 3  # news decays far faster than the score lists' 35-day window


def format_telegram_news(snapshot: dict) -> str:
    """Compact /news Telegram view: TipRanks' cached market-sentiment
    snapshot (get_market_commentary -- explicitly AI-generated per its own
    docs, labeled as such here) plus recent per-ticker headlines
    (get_assets_news), grouped by ticker with a sentiment tag. Returns ""
    if the snapshot has neither. Unlike format_telegram_screening's 35-day
    tolerance, this flags itself stale after NEWS_STALE_DAYS since news
    ages far faster than a smart-score list."""
    commentary = snapshot.get("market_commentary")
    articles = snapshot.get("news_articles") or []
    if not commentary and not articles:
        return ""

    fetched_at = datetime.fromisoformat(snapshot["fetched_at"])
    age_days = (datetime.now(timezone.utc) - fetched_at).days
    lines = [f"📰 TipRanks market pulse (fetched {snapshot['fetched_at'][:10]})"]
    if age_days > NEWS_STALE_DAYS:
        lines.append(f"⚠️ {age_days} days old -- news moves fast, treat as background only.")

    if commentary and commentary.get("overallSentiment"):
        lines.append(f"\n🌐 Overall sentiment: {commentary['overallSentiment']} (AI-generated, TipRanks)")
        if commentary.get("atmosphere"):
            lines.append(commentary["atmosphere"])
        for label, key in [("Tailwinds", "tailwinds"), ("Headwinds", "headwinds")]:
            items = commentary.get(key) or []
            if items:
                lines.append(f"{label}: " + "; ".join(items[:3]))

    if articles:
        by_ticker: dict[str, list[dict]] = {}
        for a in articles:
            by_ticker.setdefault(a.get("ticker", "?"), []).append(a)
        lines.append("\n📌 Recent headlines:")
        for ticker in sorted(by_ticker):
            top = by_ticker[ticker][0]
            sentiment = top.get("sentiment", "")
            lines.append(f"  {ticker} ({sentiment}): {top.get('title', '')}")

    return "\n".join(lines)
