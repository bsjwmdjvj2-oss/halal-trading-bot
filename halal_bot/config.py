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
    target_positions_min: int = 10
    target_positions_max: int = 12
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


@dataclass(frozen=True)
class RiskConfig:
    max_position_size_pct: float = 0.15      # ~12-15% of portfolio per position
    stop_loss_pct: float = 0.18              # 15-20% below entry
    profit_take_trigger_pct: float = 0.30    # scale out after 30%+ gain
    profit_take_scale_out_pct: float = 0.25  # sell 25% of position on trigger
    drawdown_pause_pct: float = 0.175        # pause new entries at 15-20% off peak
    max_sector_concentration_pct: float = 0.325  # ~30-35% max per sector


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
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    watchlist_path: Path = ROOT_DIR / "data" / "watchlist.yaml"
    log_dir: Path = ROOT_DIR / "logs"


CONFIG = Config()
