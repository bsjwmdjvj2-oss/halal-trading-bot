"""Daily live trading job (SPEC.md Sections 3-6, 10, 12).

Meant to run once per trading day via PythonAnywhere's Scheduled Task,
after US market close. Applies the exact same signal/risk logic the
backtester validated (halal_bot.signals, halal_bot.risk) against live
Alpaca account state.

SAFETY: defaults to DRY RUN. No order is submitted, no Telegram alert is
sent, and no state is persisted unless `live=True` is passed explicitly —
set via the LIVE_TRADING_ENABLED=true env var, read by scripts/run_daily.py.
This is deliberate: the first several runs should be inspected in the logs
before the bot is trusted to touch even a paper account unattended.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

from halal_bot.broker.alpaca_client import AccountSnapshot, AlpacaClient
from halal_bot.config import CONFIG
from halal_bot.data.prices import fetch_history
from halal_bot.live.state_store import LiveState, load_state, save_state
from halal_bot.logging_utils import log_event, log_screening, log_signal, log_trade
from halal_bot.portfolio import Position, PortfolioState
from halal_bot.risk.rules import (
    can_open_new_position,
    check_drawdown_pause,
    check_exit_risk,
    max_position_dollars,
    position_size_shares,
)
from halal_bot.screening.rules import screen_universe
from halal_bot.screening.watchlist import load_watchlist
from halal_bot.signals.strategy import generate_signals


def _days_since(date_str: str | None, today: date) -> int:
    if date_str is None:
        return 10**6  # never run before -> always due
    return (today - date.fromisoformat(date_str)).days


def _build_portfolio_state(account: AccountSnapshot, state: LiveState) -> PortfolioState:
    positions = {
        ticker: Position(
            ticker=ticker,
            sector=state.sector_map.get(ticker, "Unknown"),
            shares=p["qty"],
            entry_price=p["avg_entry_price"],
            entry_date="",
            scaled_out=ticker in state.scaled_out_tickers,
        )
        for ticker, p in account.positions.items()
    }
    return PortfolioState(cash=account.cash, positions=positions, equity_peak=state.equity_peak)


class DailyRunner:
    def __init__(self, live: bool = False):
        self.live = live
        self.messages: list[str] = []  # collected for the Telegram summary

    def _note(self, msg: str) -> None:
        self.messages.append(msg)
        print(msg)

    def run_once(self) -> str:
        today = datetime.now(timezone.utc).date()
        state = load_state()

        # Recovery model (Section 10): Alpaca's own account/positions are the
        # single source of truth, rebuilt fresh every run — the local state
        # store never claims to know what we hold, so there's nothing that
        # can drift into a "missed or duplicated trade" on restart. The only
        # local state is bookkeeping Alpaca doesn't track (equity peak,
        # manual pause, scale-out flags, screening/rebalance cadence dates).
        client = AlpacaClient()  # raises AlpacaNotConfiguredError if .env is missing
        account = client.get_account_snapshot()

        # Drop scale-out bookkeeping for anything we no longer actually hold
        # (e.g. a crash after order submit but before save_state last run).
        state.scaled_out_tickers = [t for t in state.scaled_out_tickers if t in account.positions]

        state.equity_peak = max(state.equity_peak, account.equity)

        auto_paused = check_drawdown_pause(account.equity, state.equity_peak)
        manually_paused = state.trading_paused
        if auto_paused:
            self._note(
                f"🛑 Drawdown pause active: equity ${account.equity:,.0f} vs peak "
                f"${state.equity_peak:,.0f} — no new positions will be opened."
            )
            log_event("drawdown_pause", "Auto drawdown pause active",
                       equity=account.equity, peak=state.equity_peak)

        # --- Monthly re-screen ---
        if _days_since(state.last_screen_date, today) >= CONFIG.screening.rescreen_interval_days:
            self._note("Running monthly halal re-screen...")
            instruments = load_watchlist()
            results = screen_universe(instruments)
            new_universe = []
            for r in results:
                log_screening(r.ticker, r.compliant, r.reasons, r.data_gaps)
                if r.compliant:
                    new_universe.append(r.ticker)

            previously_compliant = set(state.compliant_universe)
            newly_dropped = previously_compliant - set(new_universe)
            held_and_dropped = newly_dropped & set(account.positions)
            if held_and_dropped:
                self._note(
                    f"⚠️ COMPLIANCE REVIEW NEEDED: currently-held position(s) "
                    f"{', '.join(sorted(held_and_dropped))} failed re-screening. "
                    f"Not auto-selling — review manually and decide whether to exit."
                )
                log_event("compliance_review_needed", "Held position(s) failed re-screen",
                           tickers=sorted(held_and_dropped))

            state.compliant_universe = new_universe
            state.sector_map = {i.ticker: i.sector for i in instruments}
            state.last_screen_date = today.isoformat()
            self._note(f"Re-screen complete: {len(new_universe)}/{len(instruments)} compliant.")

        if not state.compliant_universe:
            self._note("No compliant universe available — skipping signal checks this run.")
            if self.live:
                save_state(state)
            return "\n".join(self.messages)

        portfolio = _build_portfolio_state(account, state)
        portfolio.trading_paused = manually_paused or auto_paused

        # --- Fetch latest data + signals per ticker ---
        latest_price: dict[str, float] = {}
        entry_signal: dict[str, bool] = {}
        exit_signal: dict[str, bool] = {}
        signal_reason: dict[str, str] = {}

        for ticker in state.compliant_universe:
            df = fetch_history(ticker, period_years=1, use_cache=False)
            if df.empty:
                continue
            sig = generate_signals(df)
            last = sig.iloc[-1]
            latest_price[ticker] = float(last["Close"])
            entry_signal[ticker] = bool(last["entry_signal"])
            exit_signal[ticker] = bool(last["exit_signal"])
            signal_reason[ticker] = str(last["signal_reason"])
            log_signal(ticker, str(sig.index[-1].date()), entry_signal[ticker],
                       exit_signal[ticker], signal_reason[ticker],
                       {"close": latest_price[ticker], "rsi": float(last["rsi"])})

        # --- Exits: stop-loss / profit-take / signal exit ---
        for ticker in list(portfolio.positions):
            if ticker not in latest_price:
                continue
            price = latest_price[ticker]
            pos = portfolio.positions[ticker]
            decision = check_exit_risk(pos, price)

            if decision.action == "stop_loss":
                self._submit_and_log(client, ticker, "sell", pos.shares, price, decision.reason,
                                      account.equity, category="stop_loss")
                state.scaled_out_tickers = [t for t in state.scaled_out_tickers if t != ticker]
                continue

            if decision.action == "scale_out":
                self._submit_and_log(client, ticker, "sell", decision.shares, price,
                                      decision.reason, account.equity, category="scale_out")
                if ticker not in state.scaled_out_tickers:
                    state.scaled_out_tickers.append(ticker)

            if exit_signal.get(ticker):
                remaining = pos.shares if decision.action != "scale_out" else (
                    pos.shares - decision.shares
                )
                if remaining > 0:
                    self._submit_and_log(client, ticker, "sell", remaining, price,
                                          signal_reason[ticker], account.equity,
                                          category="signal_exit")
                state.scaled_out_tickers = [t for t in state.scaled_out_tickers if t != ticker]

        # --- Monthly rebalance: trim oversized positions ---
        if _days_since(state.last_rebalance_date, today) >= CONFIG.portfolio.rebalance_interval_days:
            cap_dollars = max_position_dollars(account.equity)
            for ticker, pos in list(portfolio.positions.items()):
                if ticker not in latest_price:
                    continue
                price = latest_price[ticker]
                value = pos.market_value(price)
                if value > cap_dollars:
                    excess_shares = float(int((value - cap_dollars) / price))
                    if excess_shares > 0:
                        self._submit_and_log(client, ticker, "sell", excess_shares, price,
                                              "Monthly rebalance: trimmed to max position size cap",
                                              account.equity, category="rebalance_trim")
            state.last_rebalance_date = today.isoformat()

        # --- Entries ---
        for ticker in sorted(state.compliant_universe):
            if ticker in portfolio.positions or ticker not in latest_price:
                continue
            if not entry_signal.get(ticker):
                continue
            price = latest_price[ticker]
            sector = state.sector_map.get(ticker, "Unknown")
            dollars = min(max_position_dollars(account.equity), portfolio.cash)
            allowed, reason = can_open_new_position(portfolio, sector, dollars, latest_price)
            if not allowed:
                continue
            shares = position_size_shares(account.equity, price)
            if shares <= 0:
                continue
            self._submit_and_log(client, ticker, "buy", shares, price, signal_reason[ticker],
                                  account.equity, category="buy")
            portfolio.cash -= shares * price  # keep local view consistent for subsequent iterations

        summary = self._daily_summary(account, portfolio)
        self._note(summary)

        if self.live:
            save_state(state)
            asyncio.run(self._send_telegram_summary())

        return "\n".join(self.messages)

    _CATEGORY_LABELS = {
        "buy": ("🟢", "BUY"),
        "stop_loss": ("🛑", "STOP-LOSS SELL"),
        "scale_out": ("💰", "PROFIT-TAKE SELL"),
        "signal_exit": ("🔵", "SIGNAL EXIT SELL"),
        "rebalance_trim": ("⚖️", "REBALANCE TRIM"),
    }

    def _submit_and_log(self, client: AlpacaClient, ticker: str, side: str, shares: float,
                         price: float, reason: str, equity: float, category: str) -> None:
        if self.live:
            client.submit_market_order(ticker, shares, side)
        emoji, label = self._CATEGORY_LABELS.get(category, ("⚪", side.upper()))
        prefix = "" if self.live else "🧪 [DRY RUN] "
        self._note(f"{prefix}{emoji} {label}: {shares:g} {ticker} @ ~${price:.2f}\n   {reason}")
        log_trade(ticker, datetime.now(timezone.utc).date().isoformat(), side, shares, price,
                   reason, equity)

    def _daily_summary(self, account: AccountSnapshot, portfolio: PortfolioState) -> str:
        lines = [
            "📅 DAILY SUMMARY",
            f"💰 Equity: ${account.equity:,.2f}  |  💵 Cash: ${account.cash:,.2f}",
            f"📈 Open positions: {len(portfolio.positions)}/{CONFIG.portfolio.target_positions_max}",
        ]
        if portfolio.trading_paused:
            lines.append("⏸️ Status: PAUSED")
        return "\n".join(lines)

    async def _send_telegram_summary(self) -> None:
        from halal_bot.telegram.bot import send_alert

        try:
            await send_alert("\n".join(self.messages))
        except RuntimeError as e:
            log_event("telegram_send_failed", str(e))


def run_once(live: bool = False) -> str:
    return DailyRunner(live=live).run_once()
