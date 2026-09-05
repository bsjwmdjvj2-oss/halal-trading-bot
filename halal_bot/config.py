"""Central configuration for the halal growth trading bot.

All tunable thresholds live here so backtesting can sweep them and the
live bot reads the same values the backtest validated.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


@dataclass(frozen=True)
class ScreeningConfig:
    # AAOIFI-style ratio thresholds (as fraction, e.g. 0.33 = 33%)
    max_debt_ratio: float = 0.33          # total debt / market cap (avg 12mo)
    max_impure_income_ratio: float = 0.05  # interest + non-halal income / revenue
    max_cash_securities_ratio: float = 0.33  # (cash + interest-bearing securities) / market cap
    excluded_sectors: tuple[str, ...] = (
        "Alcohol", "Tobacco", "Gambling", "Conventional Banking",
        "Conventional Insurance", "Adult Entertainment", "Pork Products",
        "Conventional Defense/Weapons",
    )
    rescreen_interval_days: int = 30       # monthly re-screening cadence


@dataclass(frozen=True)
class PortfolioConfig:
    # Concurrent-position cap is NOT a flat number here -- it follows the
    # diversification table in halal_bot.risk.rules.max_positions_for_equity
    # ($300 equity -> 4 positions, up to 20 at $7,500+), the same table
    # halal_bot.research.dca_calculator uses for "how many stocks should
    # this month's contribution spread across".
    anchor_etf_slots: int = 2              # slots reserved for broad halal ETFs
    anchor_etf_tickers: tuple[str, ...] = ("SPUS", "HLAL")
    rebalance_interval_days: int = 30      # monthly rebalance
    strategy_review_interval_days: int = 90  # quarterly review


@dataclass(frozen=True)
class SignalConfig:
    sma_fast: int = 20
    sma_slow: int = 50
    rsi_period: int = 14
    rsi_oversold: float = 35.0
    rsi_overbought: float = 70.0
    volume_confirm_multiplier: float = 1.06  # entry volume must exceed N x 20d avg volume
    volume_lookback: int = 20

    # Available but NOT yet wired into generate_signals() — computed and
    # logged for research/backtesting, so any future signal change can be
    # tuned and out-of-sample validated the same way volume_confirm_multiplier
    # was, rather than bolted on live untested.
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bbands_period: int = 20
    bbands_std: float = 2.0
    adx_period: int = 14
    adx_trend_threshold: float = 25.0  # textbook cutoff: below = ranging/choppy, above = trending


@dataclass(frozen=True)
class RiskConfig:
    max_position_size_pct: float = 0.20      # raised from 0.15 -- backtested and ADOPTED
    # (scripts/backtest_position_cap_sweep.py): beat the 15% baseline on Sharpe/CAGR/
    # win-rate in full, train, AND test windows (full Sharpe 1.39->1.56, CAGR 20.3%->
    # 21.1%; test Sharpe 1.87->2.15, CAGR 28.2%->36.2%), with LOWER drawdown too
    # (fewer forced rebalance trims cutting down winners like PANW/META/BIIB). 25%
    # tested slightly better still but 30% cliffed hard in the test window (Sharpe
    # 0.44, drawdown -18.1%) -- picked 20% as the more conservative of the two
    # cleanly-passing values, since single-position concentration risk is real and
    # non-linear past some point this sweep didn't pin down precisely.
    stop_loss_pct: float = 0.18              # 15-20% below entry
    profit_take_trigger_pct: float = 0.30    # scale out after 30%+ gain
    profit_take_scale_out_pct: float = 0.25  # sell 25% of position on trigger
    drawdown_pause_pct: float = 0.175        # pause new entries at 15-20% off peak
    max_sector_concentration_pct: float = 0.325  # ~30-35% max per sector

    # Volatility-adaptive risk (ATR-based position sizing + ATR-scaled
    # trailing stop). Backtested and REJECTED on a 3-year train/test split —
    # both variants, and combined, lost on CAGR/Sharpe/drawdown/win-rate in
    # every window vs the fixed-sizing/fixed-stop baseline (see
    # BacktestEngine(vol_sizing=, trailing_stop=) docstring). Kept as a
    # documented dead end — same status as adx_filter — not a live option.
    atr_period: int = 14
    vol_sizing_reference_atr_pct: float = 0.025  # "normal" daily ATR as % of price
    vol_sizing_floor_mult: float = 0.5           # never size below 50% of the normal cap
    trailing_stop_atr_multiple: float = 3.0      # trail distance = N x ATR%, off the peak since entry
    trailing_stop_floor_pct: float = 0.10        # never trail tighter than 10%
    trailing_stop_ceiling_pct: float = 0.25      # never trail looser than 25%


@dataclass(frozen=True)
class BacktestConfig:
    starting_capital: float = 20_000.0
    lookback_years: int = 3
    commission_per_trade: float = 0.0   # Alpaca commission-free
    slippage_bps: float = 5.0           # basis points, conservative backtest assumption


@dataclass(frozen=True)
class AlpacaConfig:
    api_key: str = os.getenv("ALPACA_API_KEY", "")
    secret_key: str = os.getenv("ALPACA_SECRET_KEY", "")
    base_url: str = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")


@dataclass(frozen=True)
class HalalTerminalConfig:
    """Professional multi-methodology Shariah screening (AAOIFI, DJIM, FTSE,
    MSCI, S&P) — used as a quarterly-research cross-check alongside this
    bot's own internal ratio screen (halal_bot.screening.rules), not in the
    live monthly re-screen (see halal_bot.screening.halal_terminal_client's
    module docstring for why: token-metered API, budget doesn't cover
    screening the full watchlist every month)."""
    api_key: str = os.getenv("HT_API_KEY", "")
    base_url: str = os.getenv("HT_API_BASE_URL", "https://api.halalterminal.com")


@dataclass(frozen=True)
class TipRanksConfig:
    """TipRanks' official MCP-over-HTTP server -- a real, unattended API key
    (see halal_bot.research.tipranks_client), replacing the old chat-
    session-only snapshot-refresh pattern. Free tier here is 100 calls/
    month (a TipRanks Premium/Ultimate subscriber benefit, above the base
    50); a full watchlist refresh costs ~15 calls, so budget for roughly
    every 5 days, not daily."""
    api_key: str = os.getenv("TIPRANKS_API_KEY", "")
    base_url: str = os.getenv("TIPRANKS_API_BASE_URL", "https://mcp.tipranks.com")


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")


@dataclass(frozen=True)
class Config:
    screening: ScreeningConfig = field(default_factory=ScreeningConfig)
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    alpaca: AlpacaConfig = field(default_factory=AlpacaConfig)
    halal_terminal: HalalTerminalConfig = field(default_factory=HalalTerminalConfig)
    tipranks: TipRanksConfig = field(default_factory=TipRanksConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    watchlist_path: Path = ROOT_DIR / "data" / "watchlist.yaml"
    log_dir: Path = ROOT_DIR / "logs"


CONFIG = Config()
