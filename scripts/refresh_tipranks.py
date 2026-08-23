#!/usr/bin/env python3
"""Entry point for a PythonAnywhere Scheduled Task.

Runs daily, but only actually refreshes roughly every REFRESH_INTERVAL_DAYS
-- same self-gating pattern as scripts/run_quarterly_research.py, since
PythonAnywhere's scheduler doesn't support a "every 5 days" cadence
directly. Replaces the old chat-session-only manual snapshot refresh with
halal_bot.research.tipranks_client's real, unattended API key.

Budget: 100 calls/month, a full refresh costs ~15 -- REFRESH_INTERVAL_DAYS=5
gives ~6 refreshes/month with headroom, comfortably under quota. Aborts
without spending calls if get_usage() shows too little remaining.
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from halal_bot.logging_utils import log_event
from halal_bot.research.state import days_since, load_state, save_state
from halal_bot.research.tipranks_client import TipRanksClient
from halal_bot.research.tipranks_context import TipRanksList, save_snapshot
from halal_bot.screening.watchlist import load_watchlist

REFRESH_INTERVAL_DAYS = 5
MIN_CALLS_FOR_FULL_REFRESH = 20  # ~15 needed, small safety margin
ASSETS_DATA_BATCH_SIZE = 35
AI_SCORE_BATCH_SIZE = 25  # server-enforced cap


def _chunks(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def refresh(client: TipRanksClient) -> dict:
    tickers = [i.ticker for i in load_watchlist() if not i.is_etf]

    print(f"Fetching curated lists + market commentary for {len(tickers)} watchlist tickers...")
    smart_score = client.get_top_smart_score_stocks()
    top_rated = client.get_top_rated_stocks()
    commentary = client.get_market_commentary()

    print("Fetching consensus ratings (get_assets_data, batched)...")
    consensus_ratings: dict[str, dict] = {}
    for batch in _chunks(tickers, ASSETS_DATA_BATCH_SIZE):
        try:
            for row in client.get_assets_data(batch):
                ticker = row.get("ticker")
                if ticker and row.get("analystConsensus") is not None:
                    consensus_ratings[ticker] = {
                        "consensus": row["analystConsensus"],
                        "price_target": row.get("priceTarget"),
                        "upside_pct": row.get("priceTargetUpside"),
                    }
        except Exception as e:
            print(f"  batch failed (skipping): {e}")

    print("Fetching AI Stock Analysis scores (get_ai_stock_analysis, batched)...")
    ai_analysis_scores: dict[str, dict] = {}
    for batch in _chunks(tickers, AI_SCORE_BATCH_SIZE):
        try:
            result = client.get_ai_stock_analysis(batch)
            for row in result.get("stocks", []):
                ticker = row.get("ticker")
                if ticker and row.get("ai_score") is not None:
                    ai_analysis_scores[ticker] = {
                        "ai_score": row["ai_score"],
                        "rating": row.get("rating", ""),
                    }
        except Exception as e:
            print(f"  batch failed (skipping): {e}")

    # News for today's Smart Score 10 names only -- one call, keeps this
    # well inside budget; broader coverage isn't worth the extra calls for
    # a background refresh (unlike the one-off deep dive earlier this
    # session, run interactively with a human choosing which names mattered).
    news_tickers = [r["ticker"] for r in smart_score if r.get("ticker")][:10]
    news_articles = []
    if news_tickers:
        print(f"Fetching news for {news_tickers}...")
        try:
            news_articles = [
                {
                    "ticker": a.get("ticker"),
                    "sentiment": a.get("sentiment"),
                    "title": a.get("title"),
                    "url": a.get("url"),
                    "date": (a.get("publishTime") or a.get("date") or "")[:10],
                }
                for a in client.get_assets_news(news_tickers, count=3)
            ]
        except Exception as e:
            print(f"  news fetch failed (skipping): {e}")

    lists = [
        TipRanksList(name="top_smart_score", tickers=smart_score),
        TipRanksList(name="top_rated", tickers=top_rated),
    ]
    save_snapshot(
        lists,
        news_articles=news_articles,
        market_commentary=commentary,
        consensus_ratings=consensus_ratings,
        ai_analysis_scores=ai_analysis_scores,
    )
    return {
        "consensus_ratings": len(consensus_ratings),
        "ai_analysis_scores": len(ai_analysis_scores),
        "news_articles": len(news_articles),
    }


def main() -> int:
    today = datetime.now(timezone.utc).date()
    state = load_state()

    if days_since(state.last_tipranks_refresh_date, today) < REFRESH_INTERVAL_DAYS:
        print(f"Not due yet (last refresh: {state.last_tipranks_refresh_date or 'never'}, "
              f"interval: {REFRESH_INTERVAL_DAYS} days). Skipping.")
        return 0

    try:
        client = TipRanksClient()
    except Exception as e:
        print(f"TipRanks client not configured: {e}")
        log_event("tipranks_refresh_not_configured", str(e))
        return 1

    try:
        usage = client.get_usage()
    except Exception as e:
        print(f"Could not check usage, aborting without spending calls: {e}")
        log_event("tipranks_refresh_usage_check_failed", str(e))
        return 1

    print(f"TipRanks quota: {usage.used}/{usage.limit} used, {usage.remaining} remaining "
          f"(resets {usage.resets_at})")
    if usage.remaining < MIN_CALLS_FOR_FULL_REFRESH:
        print(f"Only {usage.remaining} calls left this month, need ~{MIN_CALLS_FOR_FULL_REFRESH} "
              f"for a full refresh -- skipping to preserve quota.")
        log_event("tipranks_refresh_low_quota", "", remaining=usage.remaining)
        return 0

    print("Refreshing TipRanks snapshot...")
    try:
        counts = refresh(client)
    except Exception as e:
        traceback.print_exc()
        log_event("tipranks_refresh_failed", str(e))
        return 1

    state.last_tipranks_refresh_date = today.isoformat()
    save_state(state)
    print(f"Snapshot refreshed: {counts}")
    log_event("tipranks_refresh_completed", "", **counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
