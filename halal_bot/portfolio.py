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
    # Idle-cash-yield reserve (e.g. SPSK) -- deliberately NOT a Position in
    # the dict above, so it can never count toward max_positions_for_equity's
    # position-count cap or the sector-concentration cap: it's treasury
    # management, not a strategic pick. Default-zero/empty, so every
    # existing caller (live trading included) is byte-for-byte unaffected
    # unless something actively calls sweep_into_reserve() below --
    # currently only halal_bot.backtest.engine's opt-in cash_sweep does.
    reserve_shares: float = 0.0
    reserve_ticker: str = ""  # "" = feature unused

    def equity(self, prices: dict[str, float]) -> float:
        holdings_value = sum(
            pos.market_value(prices.get(t, pos.entry_price)) for t, pos in self.positions.items()
        )
        reserve_value = (
            self.reserve_shares * prices.get(self.reserve_ticker, 0.0) if self.reserve_ticker else 0.0
        )
        return self.cash + holdings_value + reserve_value

    def sweep_into_reserve(self, ticker: str, price: float, min_sweep_dollars: float = 5.0) -> float:
        """Buys as much `ticker` as current cash allows, parking idle cash
        somewhere it can earn a return instead of sitting at 0%. Skips
        amounts below min_sweep_dollars (dust) rather than generating
        near-zero transactions. Returns the dollar amount actually swept
        (0.0 if skipped)."""
        if price <= 0 or self.cash < min_sweep_dollars:
            return 0.0
        self.reserve_ticker = ticker
        shares = round(self.cash / price, 6)
        cost = shares * price
        if cost <= 0:
            return 0.0
        self.cash -= cost
        self.reserve_shares += shares
        return cost

    def liquidate_reserve(self, price: float) -> float:
        """Sells the entire reserve holding back to cash -- called before
        anything that might need cash, so a swept-in reserve can never block
        a real entry from being funded. Returns the dollar amount received
        (0.0 if there was nothing to liquidate)."""
        if self.reserve_shares <= 0 or price <= 0:
            return 0.0
        proceeds = self.reserve_shares * price
        self.cash += proceeds
        self.reserve_shares = 0.0
        return proceeds

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
