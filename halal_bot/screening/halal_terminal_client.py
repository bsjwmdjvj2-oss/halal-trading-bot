"""Halal Terminal API client (https://api.halalterminal.com) — professional,
multi-methodology Shariah compliance screening (AAOIFI, DJIM, FTSE, MSCI,
S&P), each verdict traced to source ratios by a service whose entire
business is Islamic-finance compliance data.

Used as a SECOND OPINION alongside this bot's own internal AAOIFI ratio
screen (halal_bot.screening.rules) in the quarterly research step only
(halal_bot.research.quarterly_review) — not the live monthly re-screen.
Two reasons:
  1. Their quota is token-metered (free tier: 500 tokens/month, ~5-10
     tokens per screen call) — nowhere near enough to screen the full
     127-ticker watchlist every month, only enough for the small number of
     new candidates the quarterly research step proposes.
  2. This bot's live trading behavior should keep changing for reasons
     that were backtested and validated, same discipline as every signal/
     risk change this session — a third-party verdict silently altering
     which tickers are tradeable live, with no backtest of what that
     would have done historically, doesn't fit that bar. As research input
     for a human to weigh, it fits fine.

STATUS: verified against one real call (AAPL) — Halal Terminal's public
docs described the response as {overall_status, methodologies}, but the
real payload uses {is_compliant, methodology_summary, by_methodology}
instead; ScreenVerdict below matches the real, confirmed shape, not the
docs. by_methodology (richer: disposition/verified/reason/basis per
methodology) exists too if a future need wants more than the flat bool
summary this client currently reads.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from halal_bot.config import CONFIG


class HalalTerminalNotConfiguredError(RuntimeError):
    pass


@dataclass
class ScreenVerdict:
    ticker: str
    is_compliant: bool
    methodology_summary: dict[str, bool] = field(default_factory=dict)  # {"AAOIFI": True, ...}
    purification_rate: float | None = None
    explanation: str = ""
    disclaimer: str = ""  # the "religious"-severity disclaimer text, if any

    @property
    def compliant(self) -> bool:
        return self.is_compliant


class HalalTerminalClient:
    def __init__(self):
        cfg = CONFIG.halal_terminal
        if not cfg.api_key:
            raise HalalTerminalNotConfiguredError(
                "HT_API_KEY not set — fill in .env before using HalalTerminalClient"
            )
        import httpx

        self._http = httpx.Client(
            base_url=cfg.base_url,
            headers={"X-API-Key": cfg.api_key},
            timeout=20.0,
        )

    def screen(self, ticker: str) -> ScreenVerdict:
        """POST /api/screen/{ticker} — all five methodologies in one call."""
        resp = self._http.post(f"/api/screen/{ticker}")
        resp.raise_for_status()
        data = resp.json()
        disclaimers = data.get("disclaimers") or []
        religious_disclaimer = next(
            (d.get("text", "") for d in disclaimers if d.get("severity") == "religious"), ""
        )
        return ScreenVerdict(
            ticker=data.get("symbol", ticker),
            is_compliant=bool(data.get("is_compliant", False)),
            # "or {}" not a plain default: when a ticker fails the business-
            # activity screen, Halal Terminal returns this key present but
            # explicitly null (ratios were never computed), not omitted --
            # dict.get(key, {}) doesn't catch that, since the key exists.
            methodology_summary=data.get("methodology_summary") or {},
            purification_rate=data.get("purification_rate"),
            explanation=data.get("compliance_explanation") or "",
            disclaimer=religious_disclaimer,
        )

    def screen_batch(self, tickers: list[str]) -> dict[str, ScreenVerdict]:
        """Screens each ticker individually (not the bulk /api/portfolio/scan
        endpoint — its exact response shape isn't confirmed against a real
        call yet, and this is a small, infrequent batch, so the extra HTTP
        round-trips cost nothing that matters). Skips (doesn't raise on) any
        single ticker that fails, so one bad symbol doesn't lose the batch —
        callers should treat a missing key in the result as "unavailable",
        not "non-compliant"."""
        results: dict[str, ScreenVerdict] = {}
        for ticker in tickers:
            try:
                results[ticker] = self.screen(ticker)
            except Exception:
                continue
        return results
