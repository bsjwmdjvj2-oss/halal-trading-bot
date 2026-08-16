"""Loader for the maintained halal candidate universe (data/watchlist.yaml)."""
from __future__ import annotations

from dataclasses import dataclass

import yaml

from halal_bot.config import CONFIG


@dataclass(frozen=True)
class Instrument:
    ticker: str
    sector: str
    is_etf: bool = False
    name: str | None = None


def load_watchlist() -> list[Instrument]:
    with open(CONFIG.watchlist_path) as f:
        raw = yaml.safe_load(f)

    instruments = [
        Instrument(ticker=s["ticker"], sector=s["sector"], is_etf=False)
        for s in raw.get("stocks", [])
    ]
    instruments += [
        Instrument(ticker=e["ticker"], sector=e["sector"], is_etf=True, name=e.get("name"))
        for e in raw.get("etfs", [])
    ]
    return instruments


def anchor_tickers() -> set[str]:
    return set(CONFIG.portfolio.anchor_etf_tickers)
