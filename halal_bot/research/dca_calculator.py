"""Dollar-cost-averaging diversification calculator (Telegram /dca command).

Given a monthly contribution amount, looks up how many separate stocks the
diversification table (halal_bot.risk.rules._POSITION_COUNT_TABLE -- the
same table the live bot's own max_positions_for_equity uses for its
concurrent-position cap) says to spread it across, then lists that many
real candidates: tickers in the current halal-compliant universe whose
technical entry signal is actually active today, spread across sectors
before repeating one twice.

Note this is a planning/reference tool for a *contribution* amount, distinct
from the live bot's per-position dollar sizing (still 15%-of-equity,
signal-triggered, unaffected by this file either way).
"""
from __future__ import annotations

from dataclasses import dataclass

from halal_bot.data.prices import fetch_history
from halal_bot.live.state_store import LiveState
from halal_bot.risk.rules import max_positions_for_equity
from halal_bot.signals.strategy import generate_signals


def stocks_for_amount(amount: float) -> int:
    return max_positions_for_equity(amount)


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
