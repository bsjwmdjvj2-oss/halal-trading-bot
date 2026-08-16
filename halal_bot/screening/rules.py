"""AAOIFI-style compliance screen: sector exclusion + financial ratio checks.

Two-stage screen per SPEC.md Section 3:
  1. Business-activity exclusion (sector, checked against watchlist + live
     yfinance sector/industry as a cross-check).
  2. Quantitative ratio screen (debt ratio, impure-income ratio,
     cash+securities ratio) against ScreeningConfig thresholds.

This is a maintained internal screen, not a paid Zoya/Musaffa API — verify
independently before committing real capital (see README caveats).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from halal_bot.config import CONFIG
from halal_bot.data.fundamentals import Fundamentals, fetch_fundamentals
from halal_bot.screening.watchlist import Instrument

_EXCLUDED_KEYWORDS = {
    "alcohol", "brewer", "distiller", "tobacco", "cigarette", "casino",
    "gambling", "gaming", "bank", "insurance", "adult", "pork", "defense",
    "aerospace & defense", "weapons",
}


@dataclass
class ScreeningResult:
    ticker: str
    compliant: bool
    reasons: list[str] = field(default_factory=list)
    ratios: dict[str, float | None] = field(default_factory=dict)
    checked_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    data_gaps: list[str] = field(default_factory=list)


def _sector_exclusion_hit(instrument: Instrument, fundamentals: Fundamentals) -> str | None:
    haystack = " ".join(
        filter(None, [instrument.sector, fundamentals.sector, fundamentals.industry])
    ).lower()
    for keyword in _EXCLUDED_KEYWORDS:
        if keyword in haystack:
            return keyword
    return None


def screen_instrument(instrument: Instrument, fundamentals: Fundamentals | None = None) -> ScreeningResult:
    cfg = CONFIG.screening
    fundamentals = fundamentals or fetch_fundamentals(instrument.ticker)
    reasons: list[str] = []
    ratios: dict[str, float | None] = {}
    data_gaps = fundamentals.missing_fields()

    # ETFs pre-screened by the fund provider — skip ratio screen, still
    # subject to sector-keyword cross-check for audit purposes.
    if instrument.is_etf:
        hit = _sector_exclusion_hit(instrument, fundamentals)
        if hit:
            reasons.append(f"ETF sector cross-check flagged keyword '{hit}'")
            return ScreeningResult(instrument.ticker, False, reasons, ratios, data_gaps=data_gaps)
        return ScreeningResult(instrument.ticker, True, ["Pre-screened halal ETF"], ratios, data_gaps=data_gaps)

    sector_hit = _sector_exclusion_hit(instrument, fundamentals)
    if sector_hit:
        reasons.append(f"Excluded business activity: keyword '{sector_hit}'")
        return ScreeningResult(instrument.ticker, False, reasons, ratios, data_gaps=data_gaps)

    compliant = True

    if fundamentals.market_cap and fundamentals.total_debt is not None:
        debt_ratio = fundamentals.total_debt / fundamentals.market_cap
        ratios["debt_ratio"] = debt_ratio
        if debt_ratio > cfg.max_debt_ratio:
            compliant = False
            reasons.append(
                f"Debt ratio {debt_ratio:.1%} exceeds max {cfg.max_debt_ratio:.0%}"
            )
    else:
        data_gaps.append("debt_ratio (missing market_cap or total_debt)")

    if fundamentals.market_cap and fundamentals.total_cash is not None:
        cash_ratio = fundamentals.total_cash / fundamentals.market_cap
        ratios["cash_securities_ratio"] = cash_ratio
        if cash_ratio > cfg.max_cash_securities_ratio:
            compliant = False
            reasons.append(
                f"Cash+securities ratio {cash_ratio:.1%} exceeds max "
                f"{cfg.max_cash_securities_ratio:.0%}"
            )
    else:
        data_gaps.append("cash_securities_ratio (missing market_cap or total_cash)")

    if fundamentals.total_revenue and fundamentals.interest_income is not None:
        impure_ratio = fundamentals.interest_income / fundamentals.total_revenue
        ratios["impure_income_ratio"] = impure_ratio
        if impure_ratio > cfg.max_impure_income_ratio:
            compliant = False
            reasons.append(
                f"Impure income ratio {impure_ratio:.1%} exceeds max "
                f"{cfg.max_impure_income_ratio:.0%}"
            )
    else:
        # Interest income is very unreliable in free data feeds — treat as
        # an explicit data gap rather than silently passing or failing.
        data_gaps.append("impure_income_ratio (missing total_revenue or interest_income)")

    if not reasons:
        reasons.append("Passed all available ratio checks")

    return ScreeningResult(
        ticker=instrument.ticker,
        compliant=compliant,
        reasons=reasons,
        ratios=ratios,
        data_gaps=data_gaps,
    )


def screen_universe(instruments: list[Instrument]) -> list[ScreeningResult]:
    return [screen_instrument(i) for i in instruments]
