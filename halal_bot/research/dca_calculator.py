"""Dollar-cost-averaging diversification calculator (Telegram /dca command).

Purely a planning/reference tool — never touches the live daily bot's own
position sizing (that stays 15%-of-equity, signal-triggered, unchanged).
Given a monthly contribution amount, looks up how many separate stocks a
standard DCA-diversification table says to spread it across, then lists
that many real candidates: tickers in the current halal-compliant universe
whose technical entry signal is actually active today, spread across
sectors before repeating one twice (the same diversification instinct the
stock-count table itself is built on).
"""
from __future__ import annotations

from dataclasses import dataclass

from halal_bot.data.prices import fetch_history
from halal_bot.live.state_store import LiveState
from halal_bot.signals.strategy import generate_signals

# (min_inclusive, max_inclusive, stock_count) — user-supplied DCA
# diversification table. Below $100: too little to meaningfully spread,
# defaults to 1. Above $10,000: the table doesn't say, so this holds at the
# top tier's count (20) rather than guessing an extrapolation.
_STOCK_COUNT_TABLE: list[tuple[float, float, int]] = [
    (100, 199, 2),
    (200, 299, 3),
    (300, 499, 4),
    (500, 749, 5),
    (750, 999, 6),
    (1_000, 1_499, 8),
    (1_500, 1_999, 10),
    (2_000, 2_999, 12),
    (3_000, 3_999, 14),
    (4_000, 4_999, 16),
    (5_000, 7_499, 18),
    (7_500, 10_000, 20),
]


def stocks_for_amount(amount: float) -> int:
    if amount < _STOCK_COUNT_TABLE[0][0]:
        return 1
    for lo, hi, count in _STOCK_COUNT_TABLE:
        if lo <= amount <= hi:
            return count
    return _STOCK_COUNT_TABLE[-1][2]  # above $10,000 -- hold at the top tier


@dataclass
class Candidate:
    ticker: str
    sector: str
    reason: str


def pick_candidates(count: int, state: LiveState) -> tuple[list[Candidate], int]:
    """Blocking (fetches price history + signals for the whole compliant
    universe, same cost as /invest) -- call via asyncio.to_thread from an
    async Telegram handler. Returns (up to `count` candidates spread across
    sectors, total number of tickers with an active signal today) so the
    caller can tell "picked 5 of 5 available" from "picked 5 of 19
    available"."""
    signaling: list[Candidate] = []
    for ticker in sorted(state.compliant_universe):
        df = fetch_history(ticker, period_years=1, use_cache=False)
        if df.empty:
            continue
        sig = generate_signals(df)
        last = sig.iloc[-1]
        if bool(last["entry_signal"]):
            signaling.append(Candidate(
                ticker=ticker,
                sector=state.sector_map.get(ticker, "Unknown"),
                reason=str(last["signal_reason"]),
            ))

    by_sector: dict[str, list[Candidate]] = {}
    for c in signaling:
        by_sector.setdefault(c.sector, []).append(c)

    picked: list[Candidate] = []
    while len(picked) < count and any(by_sector.values()):
        for sector in list(by_sector.keys()):
            if not by_sector[sector]:
                continue
            picked.append(by_sector[sector].pop(0))
            if len(picked) >= count:
                break

    return picked, len(signaling)
