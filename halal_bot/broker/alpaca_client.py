"""Alpaca paper-trading client wrapper (SPEC.md Section 2, 10).

STATUS: skeleton only — not yet exercised against a live account. Backtest
(halal_bot.backtest) validates the signal/risk logic first; this module is
the landing spot for wiring that same logic to real Alpaca orders once
thresholds are validated (SPEC.md Section 9/13).

`reconcile_state` is an available utility that compares expected local
positions against what Alpaca actually reports. It is NOT currently called
anywhere: halal_bot.live.daily_runner's recovery model instead rebuilds
PortfolioState fresh from Alpaca on every run and never trusts any
locally-cached position state, which makes reconciliation unnecessary today.
Wire it in on startup if a future design ever introduces cached/trusted
local position state between runs.
"""
from __future__ import annotations

from dataclasses import dataclass

from halal_bot.config import CONFIG
from halal_bot.logging_utils import log_event


class AlpacaNotConfiguredError(RuntimeError):
    pass


@dataclass
class AccountSnapshot:
    equity: float
    cash: float
    positions: dict[str, dict]  # ticker -> {"qty": float, "avg_entry_price": float, "market_value": float}


class AlpacaClient:
    def __init__(self):
        if not CONFIG.alpaca.api_key or not CONFIG.alpaca.secret_key:
            raise AlpacaNotConfiguredError(
                "ALPACA_API_KEY / ALPACA_SECRET_KEY not set — fill in .env before using AlpacaClient"
            )
        # Deferred import: keeps `alpaca-py` optional for anyone only running backtests.
        from alpaca.trading.client import TradingClient

        self._client = TradingClient(
            CONFIG.alpaca.api_key,
            CONFIG.alpaca.secret_key,
            paper="paper-api" in CONFIG.alpaca.base_url,
        )

    def get_account_snapshot(self) -> AccountSnapshot:
        account = self._client.get_account()
        positions = self._client.get_all_positions()
        return AccountSnapshot(
            equity=float(account.equity),
            cash=float(account.cash),
            positions={
                p.symbol: {
                    "qty": float(p.qty),
                    "avg_entry_price": float(p.avg_entry_price),
                    "market_value": float(p.market_value),
                }
                for p in positions
            },
        )

    def get_open_order_symbols(self) -> set[str]:
        """Symbols with a currently open (unfilled) order.

        A market order shows up here immediately on submission — well before
        it fills and appears in get_account_snapshot().positions. Checking
        this in addition to positions is what actually prevents a double-buy
        if the daily job is ever invoked twice in quick succession (manual
        re-run, overlapping scheduled runs): positions alone can't detect an
        order that's still in flight.
        """
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = self._client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
        return {o.symbol for o in orders}

    def submit_market_order(self, ticker: str, qty: float, side: str) -> str:
        """side: 'buy' | 'sell'. qty may be fractional -- Alpaca accepts a
        fractional qty directly on market orders (time_in_force=DAY, already
        the case here; fractional orders can't use GTC or extended hours,
        neither of which this bot uses). Returns the Alpaca order id."""
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        order = self._client.submit_order(
            MarketOrderRequest(
                symbol=ticker,
                qty=qty,
                side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
        )
        log_event("order_submitted", f"{side} {qty} {ticker}", order_id=str(order.id))
        return str(order.id)

    def reconcile_state(self, expected_positions: dict[str, float]) -> list[str]:
        """Compares expected {ticker: shares} against Alpaca's actual positions.

        Returns a list of human-readable discrepancy messages. NOT currently
        called anywhere — see the module docstring. Available for a future
        design that caches local position state between runs.
        """
        actual = self.get_account_snapshot().positions
        discrepancies = []
        for ticker, expected_qty in expected_positions.items():
            actual_qty = actual.get(ticker, {}).get("qty", 0.0)
            if abs(actual_qty - expected_qty) > 1e-6:
                discrepancies.append(
                    f"{ticker}: expected {expected_qty} shares, Alpaca reports {actual_qty}"
                )
        for ticker in actual:
            if ticker not in expected_positions:
                discrepancies.append(f"{ticker}: unexpected position on Alpaca, not in local state")

        if discrepancies:
            log_event("reconciliation_mismatch", "State mismatch on reconcile", details=discrepancies)
        else:
            log_event("reconciliation_ok", "Local state matches Alpaca")
        return discrepancies
