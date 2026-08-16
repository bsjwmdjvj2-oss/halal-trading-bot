"""Best-effort fundamentals fetcher (yfinance) for the AAOIFI-style screen.

yfinance's fields are inconsistently populated across tickers, so every
value here is optional. Missing data is surfaced to the caller rather than
silently assumed compliant or non-compliant — the screening module decides
how to treat gaps.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import yfinance as yf


@dataclass
class Fundamentals:
    ticker: str
    market_cap: float | None
    total_debt: float | None
    total_cash: float | None
    total_revenue: float | None
    interest_income: float | None
    sector: str | None
    industry: str | None

    def missing_fields(self) -> list[str]:
        return [
            name
            for name in ("market_cap", "total_debt", "total_cash", "total_revenue")
            if getattr(self, name) is None
        ]


def _clean(value) -> float | None:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(value):
        return None
    return value


def fetch_fundamentals(ticker: str) -> Fundamentals:
    t = yf.Ticker(ticker)
    info = t.info or {}

    interest_income = None
    try:
        income_stmt = t.income_stmt
        if income_stmt is not None and "Interest Income" in income_stmt.index:
            interest_income = _clean(income_stmt.loc["Interest Income"].iloc[0])
    except Exception:
        interest_income = None

    return Fundamentals(
        ticker=ticker,
        market_cap=_clean(info.get("marketCap")),
        total_debt=_clean(info.get("totalDebt")),
        total_cash=_clean(info.get("totalCash")),
        total_revenue=_clean(info.get("totalRevenue")),
        interest_income=interest_income,
        sector=info.get("sector"),
        industry=info.get("industry"),
    )
