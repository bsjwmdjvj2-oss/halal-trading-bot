"""Plain-English portfolio summaries via LLM (SPEC.md Section 8).

This agent only narrates state the rules engine already acted on — it does
not make trading decisions. Call it low-frequency (daily/weekly), never per
signal check, to keep API cost down.

STATUS: skeleton — functional once ANTHROPIC_API_KEY is set, but nothing
schedules it yet (that's part of the live trading loop, not yet built).
"""
from __future__ import annotations

import os

_SYSTEM_PROMPT = (
    "You summarize a halal-screened stock portfolio's current state for a Telegram "
    "message. Maximum 2 short sentences, under 35 words total — the exact figures and "
    "every trade are already listed separately below your summary, so do not repeat "
    "them individually. Just give the one or two things that actually matter: overall "
    "P&L direction/magnitude, and only a truly notable event (a stop-loss, a big move) "
    "if there is one. You are narrating facts already decided by a rules engine — never "
    "suggest trades, never give financial advice, never speculate about future "
    "performance. No filler like 'the portfolio' or restating the date."
)


def generate_portfolio_summary(
    equity: float,
    cash: float,
    positions: dict[str, dict],
    recent_trades: list[dict],
    period_return_pct: float,
) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set — fill in .env before using the summary agent")

    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)

    positions_text = "\n".join(
        f"  {ticker}: {p['qty']} shares, market value ${p['market_value']:.2f}"
        for ticker, p in positions.items()
    ) or "  (no open positions)"
    trades_text = "\n".join(
        f"  {t['date']} {t['action']} {t['ticker']} {t['shares']} @ ${t['price']:.2f}"
        for t in recent_trades
    ) or "  (no trades this period)"

    user_prompt = (
        f"Portfolio equity: ${equity:,.2f}\n"
        f"Cash: ${cash:,.2f}\n"
        f"Period return: {period_return_pct:+.2f}%\n"
        f"Open positions:\n{positions_text}\n"
        f"Recent trades:\n{trades_text}\n"
    )

    message = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=120,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return message.content[0].text
