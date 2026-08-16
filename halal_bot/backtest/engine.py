"""Event-driven backtest engine (SPEC.md Section 9).

Simulates the same rules the live bot will use: signal entries/exits
(halal_bot.signals.strategy), position sizing / stop-loss / profit-take /
drawdown-pause / sector-cap (halal_bot.risk.rules), and a simple monthly
rebalance that trims any position that has drifted above the max position
size.

LIMITATION: the halal compliance screen is run once against *current*
fundamentals (halal_bot.screening) — free data sources don't provide
point-in-time historical fundamentals, so this backtest cannot re-run the
monthly re-screen retroactively. Treat backtest results as validating the
signal/risk logic, not the screening logic. This is called out again in the
generated report.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from halal_bot.config import CONFIG
from halal_bot.backtest.metrics import BacktestMetrics, compute_metrics
from halal_bot.portfolio import Position, PortfolioState
from halal_bot.risk.rules import (
    can_open_new_position,
    check_exit_risk,
    max_position_dollars,
    position_size_shares,
)
from halal_bot.signals.strategy import generate_signals

REBALANCE_INTERVAL_TRADING_DAYS = 21  # ~1 month


@dataclass
class Trade:
    date: str
    ticker: str
    action: str
    shares: float
    price: float
    pnl: float | None = None
    reason: str = ""


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: list[Trade] = field(default_factory=list)
    metrics: BacktestMetrics | None = None


class BacktestEngine:
    def __init__(self, price_data: dict[str, pd.DataFrame], sector_map: dict[str, str]):
        """price_data: ticker -> OHLCV DataFrame (already screened as halal-compliant)."""
        self.sector_map = sector_map
        self.signals = {t: generate_signals(df) for t, df in price_data.items() if not df.empty}
        self.master_dates = sorted(set().union(*[df.index for df in self.signals.values()]))

        self.close_ff = {
            t: df["Close"].reindex(self.master_dates).ffill() for t, df in self.signals.items()
        }
        self.entry_native = {
            t: df["entry_signal"].reindex(self.master_dates, fill_value=False)
            for t, df in self.signals.items()
        }
        self.exit_native = {
            t: df["exit_signal"].reindex(self.master_dates, fill_value=False)
            for t, df in self.signals.items()
        }
        self.reason_native = {
            t: df["signal_reason"].reindex(self.master_dates, fill_value="")
            for t, df in self.signals.items()
        }
        self.has_data = {
            t: df["Close"].reindex(self.master_dates).notna() for t, df in self.signals.items()
        }

    def _prices_on(self, date) -> dict[str, float]:
        return {
            t: self.close_ff[t][date]
            for t in self.signals
            if self.has_data[t][date] and not pd.isna(self.close_ff[t][date])
        }

    def run(self, start_date=None, end_date=None) -> BacktestResult:
        """start_date/end_date (optional): restrict simulated trading + equity
        tracking to this date range, while indicators still see the full
        price history for warm-up (SMA/RSI are backward-looking only, so
        this introduces no lookahead). Used for train/test out-of-sample
        splits without re-fetching or re-warming indicators per split.
        """
        cfg = CONFIG.backtest
        slippage = cfg.slippage_bps / 10_000

        portfolio = PortfolioState(cash=cfg.starting_capital)
        trades: list[Trade] = []
        equity_points: list[tuple] = []
        was_paused = False

        trading_dates = [
            d for d in self.master_dates
            if (start_date is None or d >= start_date) and (end_date is None or d <= end_date)
        ]

        for i, date in enumerate(trading_dates):
            prices = self._prices_on(date)
            if not prices:
                continue

            portfolio.update_peak(prices)
            equity = portfolio.equity(prices)

            from halal_bot.risk.rules import check_drawdown_pause

            paused_now = check_drawdown_pause(equity, portfolio.equity_peak)
            portfolio.trading_paused = paused_now
            if paused_now and not was_paused:
                trades.append(Trade(str(date.date()), "PORTFOLIO", "drawdown_pause", 0, 0,
                                     reason=f"Equity {equity:.0f} vs peak {portfolio.equity_peak:.0f}"))
            was_paused = paused_now

            # --- Exits: stop-loss / profit-take / signal exit ---
            for ticker in list(portfolio.positions):
                if ticker not in prices:
                    continue
                price = prices[ticker]
                pos = portfolio.positions[ticker]
                decision = check_exit_risk(pos, price)

                if decision.action == "stop_loss":
                    sell_price = price * (1 - slippage)
                    pnl = (sell_price - pos.entry_price) * pos.shares
                    portfolio.cash += pos.shares * sell_price
                    trades.append(Trade(str(date.date()), ticker, "stop_loss", pos.shares,
                                         sell_price, pnl, decision.reason))
                    del portfolio.positions[ticker]
                    continue

                if decision.action == "scale_out":
                    sell_price = price * (1 - slippage)
                    pnl = (sell_price - pos.entry_price) * decision.shares
                    portfolio.cash += decision.shares * sell_price
                    pos.shares -= decision.shares
                    pos.scaled_out = True
                    trades.append(Trade(str(date.date()), ticker, "scale_out", decision.shares,
                                         sell_price, pnl, decision.reason))

                if ticker in self.exit_native and bool(self.exit_native[ticker].get(date, False)):
                    sell_price = price * (1 - slippage)
                    pnl = (sell_price - pos.entry_price) * pos.shares
                    portfolio.cash += pos.shares * sell_price
                    trades.append(Trade(str(date.date()), ticker, "signal_exit", pos.shares,
                                         sell_price, pnl, self.reason_native[ticker].get(date, "")))
                    del portfolio.positions[ticker]

            # --- Monthly rebalance: trim oversized positions ---
            if i % REBALANCE_INTERVAL_TRADING_DAYS == 0 and i > 0:
                equity = portfolio.equity(prices)
                cap_dollars = max_position_dollars(equity)
                for ticker, pos in list(portfolio.positions.items()):
                    if ticker not in prices:
                        continue
                    price = prices[ticker]
                    value = pos.market_value(price)
                    if value > cap_dollars:
                        excess_shares = float(int((value - cap_dollars) / price))
                        if excess_shares > 0:
                            sell_price = price * (1 - slippage)
                            pnl = (sell_price - pos.entry_price) * excess_shares
                            portfolio.cash += excess_shares * sell_price
                            pos.shares -= excess_shares
                            trades.append(Trade(str(date.date()), ticker, "rebalance_trim",
                                                 excess_shares, sell_price, pnl,
                                                 "Trimmed to max position size cap"))

            # --- Entries ---
            equity = portfolio.equity(prices)
            for ticker in sorted(self.signals):
                if ticker in portfolio.positions or ticker not in prices:
                    continue
                if not bool(self.entry_native[ticker].get(date, False)):
                    continue
                price = prices[ticker]
                sector = self.sector_map.get(ticker, "Unknown")
                dollars = min(max_position_dollars(equity), portfolio.cash)
                allowed, _reason = can_open_new_position(portfolio, sector, dollars, prices)
                if not allowed:
                    continue
                buy_price = price * (1 + slippage)
                shares = position_size_shares(equity, buy_price)
                cost = shares * buy_price
                if shares <= 0 or cost > portfolio.cash:
                    continue
                portfolio.cash -= cost
                portfolio.positions[ticker] = Position(
                    ticker=ticker, sector=sector, shares=shares,
                    entry_price=buy_price, entry_date=str(date.date()),
                )
                trades.append(Trade(str(date.date()), ticker, "buy", shares, buy_price,
                                     reason=self.reason_native[ticker].get(date, "")))

            equity_points.append((date, portfolio.equity(self._prices_on(date))))

        equity_curve = pd.Series(
            [e for _, e in equity_points], index=[d for d, _ in equity_points], name="equity"
        )
        closed_pnls = [t.pnl for t in trades if t.pnl is not None]
        metrics = compute_metrics(equity_curve, closed_pnls) if len(equity_curve) > 1 else None

        return BacktestResult(equity_curve=equity_curve, trades=trades, metrics=metrics)
