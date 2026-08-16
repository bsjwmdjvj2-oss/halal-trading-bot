"""Shared portfolio state used by both the risk rules and the backtest engine."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Position:
    ticker: str
    sector: str
    shares: float
    entry_price: float
    entry_date: str
    scaled_out: bool = False  # True once the 30%-gain scale-out has fired

    def market_value(self, price: float) -> float:
        return self.shares * price

    def unrealized_return(self, price: float) -> float:
        return (price - self.entry_price) / self.entry_price


@dataclass
class PortfolioState:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    equity_peak: float = 0.0
    trading_paused: bool = False

    def equity(self, prices: dict[str, float]) -> float:
        holdings_value = sum(
            pos.market_value(prices[t]) for t, pos in self.positions.items() if t in prices
        )
        return self.cash + holdings_value

    def sector_exposure(self, sector: str, prices: dict[str, float]) -> float:
        equity = self.equity(prices)
        if equity <= 0:
            return 0.0
        sector_value = sum(
            pos.market_value(prices[t])
            for t, pos in self.positions.items()
            if pos.sector == sector and t in prices
        )
        return sector_value / equity

    def update_peak(self, prices: dict[str, float]) -> None:
        self.equity_peak = max(self.equity_peak, self.equity(prices))
