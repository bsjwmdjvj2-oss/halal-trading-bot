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
from halal_bot.logging_utils import (
    log_equity_snapshot,
    log_event,
    log_screening,
    log_signal,
    log_trade,
)
from halal_bot.portfolio import Position, PortfolioState
from halal_bot.risk.rules import (
    can_open_new_position,
    check_drawdown_pause,
    check_exit_risk,
    max_position_dollars,
    max_positions_for_equity,
    position_size_shares,
)
from halal_bot.screening.rules import screen_universe
from halal_bot.screening.watchlist import load_watchlist
from halal_bot.signals.strategy import generate_signals


MIN_ORDER_DOLLARS = 1.0  # Alpaca rejects fractional orders below $1 notional


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
        self.trade_records: list[dict] = []  # structured, for the AI summary agent

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

        # Guards against double-buying if this job is ever invoked twice in
        # quick succession (manual re-run, overlapping scheduled runs) before
        # the first run's order has filled — positions alone can't catch
        # that, since a just-submitted market order doesn't appear as a
        # position until it fills, but it does appear here immediately.
        open_order_symbols = client.get_open_order_symbols() if self.live else set()

        # Drop scale-out bookkeeping for anything we no longer actually hold
        # (e.g. a crash after order submit but before save_state last run).
        state.scaled_out_tickers = [t for t in state.scaled_out_tickers if t in account.positions]

        period_return_pct = (
            (account.equity - state.previous_equity) / state.previous_equity * 100
            if state.previous_equity > 0 else 0.0
        )

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

        # Held positions that fell out of the compliant universe (failed
        # re-screen, pending manual compliance review — see above) are
        # deliberately not signal-traded, but still need a price so
        # stop-loss/profit-take can keep protecting them below.
        for ticker in account.positions:
            if ticker in latest_price or ticker in state.compliant_universe:
                continue
            df = fetch_history(ticker, period_years=1, use_cache=False)
            if not df.empty:
                latest_price[ticker] = float(df["Close"].iloc[-1])

        # --- Exits: stop-loss / profit-take / signal exit ---
        for ticker in list(portfolio.positions):
            if ticker not in latest_price:
                continue
            if ticker in open_order_symbols:
                continue
            price = latest_price[ticker]
            pos = portfolio.positions[ticker]
            decision = check_exit_risk(pos, price)

            if decision.action == "stop_loss":
                pnl = (price - pos.entry_price) * pos.shares
                if self._submit_and_log(client, ticker, "sell", pos.shares, price, decision.reason,
                                         account.equity, category="stop_loss", pnl=pnl):
                    portfolio.apply_sell(ticker, pos.shares, price)
                    state.scaled_out_tickers = [t for t in state.scaled_out_tickers if t != ticker]
                continue

            if decision.action == "scale_out":
                pnl = (price - pos.entry_price) * decision.shares
                if self._submit_and_log(client, ticker, "sell", decision.shares, price,
                                         decision.reason, account.equity, category="scale_out",
                                         pnl=pnl):
                    portfolio.apply_sell(ticker, decision.shares, price)
                    if ticker not in state.scaled_out_tickers:
                        state.scaled_out_tickers.append(ticker)

            # Anchor ETFs don't get sold on a death cross — held independent
            # of the technical signal by design (see anchor allocation block
            # below). Without this, a death cross sells it and the anchor
            # block immediately rebuys it same-run, churning for no purpose.
            is_anchor = ticker in CONFIG.portfolio.anchor_etf_tickers[:CONFIG.portfolio.anchor_etf_slots]
            if not is_anchor and exit_signal.get(ticker) and pos.shares > 0:
                remaining = pos.shares
                pnl = (price - pos.entry_price) * remaining
                if self._submit_and_log(client, ticker, "sell", remaining, price,
                                         signal_reason[ticker], account.equity,
                                         category="signal_exit", pnl=pnl):
                    portfolio.apply_sell(ticker, remaining, price)
                    state.scaled_out_tickers = [t for t in state.scaled_out_tickers if t != ticker]

        # --- Monthly rebalance: trim oversized positions ---
        if _days_since(state.last_rebalance_date, today) >= CONFIG.portfolio.rebalance_interval_days:
            cap_dollars = max_position_dollars(account.equity)
            for ticker, pos in list(portfolio.positions.items()):
                if ticker not in latest_price:
                    continue
                if ticker in open_order_symbols:
                    continue
                price = latest_price[ticker]
                value = pos.market_value(price)
                if value > cap_dollars:
                    excess_shares = round((value - cap_dollars) / price, 6)
                    if excess_shares > 0:
                        pnl = (price - pos.entry_price) * excess_shares
                        if self._submit_and_log(client, ticker, "sell", excess_shares, price,
                                                 "Monthly rebalance: trimmed to max position size cap",
                                                 account.equity, category="rebalance_trim", pnl=pnl):
                            portfolio.apply_sell(ticker, excess_shares, price)
            state.last_rebalance_date = today.isoformat()

        # --- Anchor allocation (SPEC.md Section 3): force-hold configured
        # anchor ETFs at the standard position size, independent of the
        # technical signal — mirrors halal_bot.backtest.engine exactly.
        # Re-buys automatically if ever stopped out, since "not currently
        # held" is the only gate (plus the open-order check below, which is
        # what actually prevents a double-buy on a same-day re-run).
        for ticker in CONFIG.portfolio.anchor_etf_tickers[:CONFIG.portfolio.anchor_etf_slots]:
            if (ticker not in state.compliant_universe or ticker in portfolio.positions
                    or ticker in open_order_symbols or ticker not in latest_price):
                continue
            price = latest_price[ticker]
            sector = state.sector_map.get(ticker, "Unknown")
            dollars = min(max_position_dollars(account.equity), portfolio.cash)
            allowed, reason = can_open_new_position(portfolio, sector, dollars, latest_price)
            if not allowed:
                continue
            shares = position_size_shares(account.equity, price)
            if shares * price < MIN_ORDER_DOLLARS or shares * price > portfolio.cash:
                continue
            if self._submit_and_log(client, ticker, "buy", shares, price,
                                     "Anchor allocation (forced, independent of signal)",
                                     account.equity, category="anchor_buy"):
                portfolio.apply_buy(ticker, sector, shares, price)

        # --- Entries ---
        # Smart-Score-10 tickers get first claim on today's open slots when
        # more tickers signal than there's room for -- a same-day priority
        # only, never a gate (still requires an active entry_signal like
        # everything else here). See tipranks_context.smart_score_10_tickers
        # for why this is deliberately live-only, not backtested.
        # Strong Buy consensus is a HARD GATE (not just a priority like Smart
        # Score): a ticker needs both an active technical entry_signal AND
        # a current TipRanks "Strong Buy" consensus to be bought. Fails
        # closed -- see tipranks_context.strong_buy_tickers -- so a ticker
        # missing from the snapshot or a stale snapshot blocks ALL entries
        # here, not just the ones without Strong Buy.
        from halal_bot.research.tipranks_context import (
            load_snapshot, smart_score_10_tickers, strong_buy_tickers,
        )
        tipranks_snapshot = load_snapshot()
        smart_score_priority = smart_score_10_tickers(tipranks_snapshot)
        strong_buy = strong_buy_tickers(tipranks_snapshot)
        if not strong_buy:
            self._note(
                "⚠️ No TipRanks Strong-Buy data available (snapshot missing, stale, or "
                "empty) -- ALL new entries are blocked until it's refreshed."
            )

        for ticker in sorted(state.compliant_universe, key=lambda t: (t not in smart_score_priority, t)):
            if (ticker in portfolio.positions or ticker in open_order_symbols
                    or ticker not in latest_price):
                continue
            if not entry_signal.get(ticker):
                continue
            if ticker not in strong_buy:
                continue
            price = latest_price[ticker]
            sector = state.sector_map.get(ticker, "Unknown")
            dollars = min(max_position_dollars(account.equity), portfolio.cash)
            allowed, reason = can_open_new_position(portfolio, sector, dollars, latest_price)
            if not allowed:
                continue
            shares = position_size_shares(account.equity, price)
            if shares * price < MIN_ORDER_DOLLARS or shares * price > portfolio.cash:
                continue
            if self._submit_and_log(client, ticker, "buy", shares, price, signal_reason[ticker],
                                     account.equity, category="buy"):
                portfolio.apply_buy(ticker, sector, shares, price)  # keeps local view consistent for subsequent iterations

        summary = self._daily_summary(account, portfolio)
        self._note(summary)

        if self.live:
            log_equity_snapshot(today.isoformat(), account.equity, account.cash)
            state.previous_equity = account.equity
            save_state(state)
            asyncio.run(self._send_telegram_summary(account, portfolio, period_return_pct))

        return "\n".join(self.messages)

    _CATEGORY_LABELS = {
        "buy": ("🟢", "BUY"),
        "anchor_buy": ("⚓", "ANCHOR BUY"),
        "stop_loss": ("🛑", "STOP-LOSS SELL"),
        "scale_out": ("💰", "PROFIT-TAKE SELL"),
        "signal_exit": ("🔵", "SIGNAL EXIT SELL"),
        "rebalance_trim": ("⚖️", "REBALANCE TRIM"),
    }

    def _submit_and_log(self, client: AlpacaClient, ticker: str, side: str, shares: float,
                         price: float, reason: str, equity: float, category: str,
                         pnl: float | None = None) -> bool:
        """Returns True if the trade actually happened (or this is a dry run)
        so callers only update local cash/position bookkeeping for trades
        that really executed. A submission failure is caught here rather
        than left to crash run_once — that would lose state bookkeeping for
        every earlier trade that already executed this same run."""
        if self.live:
            try:
                client.submit_market_order(ticker, shares, side)
            except Exception as e:
                log_event("order_submit_failed", f"{side} {shares} {ticker}: {e}",
                           category=category)
                self._note(f"⚠️ ORDER FAILED: {side} {shares:g} {ticker} @ ~${price:.2f} — {e}")
                return False
        emoji, label = self._CATEGORY_LABELS.get(category, ("⚪", side.upper()))
        prefix = "" if self.live else "🧪 [DRY RUN] "
        pnl_text = f" (P&L {pnl:+,.2f})" if pnl is not None else ""
        self._note(f"{prefix}{emoji} {label}: {shares:g} {ticker} @ ~${price:.2f}{pnl_text}\n   {reason}")
        trade_date = datetime.now(timezone.utc).date().isoformat()
        log_trade(ticker, trade_date, side, shares, price, reason, equity,
                   category=category, pnl=pnl, dry_run=not self.live)
        self.trade_records.append({
            "date": trade_date, "action": category, "ticker": ticker,
            "shares": shares, "price": price,
        })
        return True

    def _daily_summary(self, account: AccountSnapshot, portfolio: PortfolioState) -> str:
        lines = [
            "📅 DAILY SUMMARY",
            f"💰 Equity: ${account.equity:,.2f}  |  💵 Cash: ${account.cash:,.2f}",
            f"📈 Open positions: {len(portfolio.positions)}/{max_positions_for_equity(account.equity)}",
        ]
        if portfolio.trading_paused:
            lines.append("⏸️ Status: PAUSED")
        return "\n".join(lines)

    def _ai_narrate(self, account: AccountSnapshot, portfolio: PortfolioState,
                     period_return_pct: float) -> str | None:
        """Section 8: low-frequency (once/day, this call site only) plain-English
        narration of state the rules engine already decided — never a trade decision.
        Degrades silently to None (deterministic summary still sends) if no
        ANTHROPIC_API_KEY is set or the API call fails for any reason."""
        import os
        if not os.getenv("ANTHROPIC_API_KEY"):
            return None
        from halal_bot.ai_summary.summarizer import generate_portfolio_summary

        try:
            return generate_portfolio_summary(
                equity=account.equity,
                cash=account.cash,
                positions=account.positions,
                recent_trades=self.trade_records,
                period_return_pct=period_return_pct,
            )
        except Exception as e:
            log_event("ai_summary_failed", str(e))
            return None

    async def _send_telegram_summary(self, account: AccountSnapshot, portfolio: PortfolioState,
                                       period_return_pct: float) -> None:
        from halal_bot.telegram.bot import send_alert

        narration = self._ai_narrate(account, portfolio, period_return_pct)
        parts = ([f"🤖 {narration}", ""] if narration else []) + self.messages
        try:
            await send_alert("\n".join(parts))
        except RuntimeError as e:
            log_event("telegram_send_failed", str(e))


def run_once(live: bool = False) -> str:
    return DailyRunner(live=live).run_once()
