"""Historical OHLCV fetcher (yfinance) with on-disk caching for backtests."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yfinance as yf

from halal_bot.config import ROOT_DIR

_CACHE_DIR = ROOT_DIR / "data" / "price_cache"


def fetch_history(ticker: str, period_years: int, use_cache: bool = True) -> pd.DataFrame:
    """Daily OHLCV for the trailing `period_years`. Empty DataFrame on failure."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _CACHE_DIR / f"{ticker}_{period_years}y.parquet"

    if use_cache and cache_path.exists():
        return pd.read_parquet(cache_path)

    df = yf.Ticker(ticker).history(period=f"{period_years}y", auto_adjust=True)
    if df.empty:
        return df

    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    if use_cache:
        df.to_parquet(cache_path)
    return df


def fetch_universe_history(
    tickers: list[str], period_years: int, use_cache: bool = True
) -> dict[str, pd.DataFrame]:
    result = {}
    for ticker in tickers:
        df = fetch_history(ticker, period_years, use_cache=use_cache)
        if not df.empty:
            result[ticker] = df
    return result
