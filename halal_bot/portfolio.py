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
    highest_price: float = 0.0  # running high-water-mark since entry, for trailing_stop

    def __post_init__(self) -> None:
        if self.highest_price <= 0:
            self.highest_price = self.entry_price

    def market_value(self, price: float) -> float:
        return self.shares * price

    def unrealized_return(self, price: float) -> float:
        return (price - self.entry_price) / self.entry_price

    def update_high(self, price: float) -> None:
        if price > self.highest_price:
            self.highest_price = price


@dataclass
class PortfolioState:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    equity_peak: float = 0.0
    trading_paused: bool = False

    def equity(self, prices: dict[str, float]) -> float:
        holdings_value = sum(
            pos.market_value(prices.get(t, pos.entry_price)) for t, pos in self.positions.items()
        )
        return self.cash + holdings_value

    def sector_exposure(self, sector: str, prices: dict[str, float]) -> float:
        equity = self.equity(prices)
        if equity <= 0:
            return 0.0
        sector_value = sum(
            pos.market_value(prices.get(t, pos.entry_price))
            for t, pos in self.positions.items()
            if pos.sector == sector
        )
        return sector_value / equity

    def update_peak(self, prices: dict[str, float]) -> None:
        self.equity_peak = max(self.equity_peak, self.equity(prices))

    def apply_buy(self, ticker: str, sector: str, shares: float, price: float) -> None:
        """Submit-side bookkeeping for a buy: keeps cash and positions in sync
        so every buy call site doesn't have to hand-roll it (and risk, as one
        did, forgetting the positions half entirely)."""
        self.cash -= shares * price
        if ticker in self.positions:
            pos = self.positions[ticker]
            total_shares = pos.shares + shares
            pos.entry_price = (pos.entry_price * pos.shares + price * shares) / total_shares
            pos.shares = total_shares
        else:
            self.positions[ticker] = Position(
                ticker=ticker, sector=sector, shares=shares, entry_price=price, entry_date="",
            )

    def apply_sell(self, ticker: str, shares: float, price: float) -> None:
        """Sell-side counterpart to apply_buy: decrements cash, decrements
        shares, and removes the position once fully sold — the one place
        this bookkeeping happens instead of it being repeated per call site."""
        self.cash += shares * price
        pos = self.positions[ticker]
        pos.shares -= shares
        if pos.shares <= 0:
            del self.positions[ticker]
