"""Event-driven backtest engine (SPEC.md Section 9).

Simulates the same rules the live bot will use: signal entries/exits
(halal_bot.signals.strategy), position sizing / stop-loss / profit-take /
drawdown-pause / sector-cap (halal_bot.risk.rules), a simple monthly
rebalance that trims any position that has drifted above the max position
size, and forced anchor-ETF allocation (SPEC.md Section 3) independent of
the technical signal.

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
    volatility_size_multiplier,
)
from halal_bot.signals.indicators import atr_pct as compute_atr_pct
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
    total_contributed: float = 0.0


class BacktestEngine:
    def __init__(self, price_data: dict[str, pd.DataFrame], sector_map: dict[str, str],
                 adx_filter: bool = False, macd_filter: bool = True,
                 vol_sizing: bool = False, trailing_stop: bool = False,
                 monthly_contribution: float = 0.0, rank_entries: bool = False,
                 rank_entries_by_macd: bool = False, min_hold_days: int = 0):
        """price_data: ticker -> OHLCV DataFrame (already screened as halal-compliant).
        adx_filter, macd_filter: forwarded to generate_signals() — see its docstring.
        vol_sizing, trailing_stop: ATR-based position sizing / ATR-scaled
        trailing stop (halal_bot.risk.rules). Backtested and REJECTED on a
        3-year train/test split — every variant (each alone and combined)
        lost on CAGR, Sharpe, max drawdown, AND win rate vs the fixed
        baseline in every window (full/train/test). vol_sizing shrinks bets
        on exactly the volatile growth names that are this strategy's
        return engine; trailing_stop cuts trend-followers short on ordinary
        pullbacks well before the death-cross exit would ever fire, and gets
        whipsawed hardest during the same broad pullbacks it was meant to
        protect against. Kept as a documented dead end (same status as
        adx_filter) — both default False and should stay that way.
        monthly_contribution (opt-in, default 0.0 = unchanged): added to cash
        on the same ~monthly cadence as the position-size rebalance. Note
        this makes the engine's own total_return_pct/cagr_pct (equity start
        vs end) overstate trading performance, since part of the equity
        growth is just deposited cash, not gains — see BacktestResult.
        total_contributed and halal_bot.backtest.report's net-trading-gain
        line for the contribution-adjusted figure.
        rank_entries (opt-in, default False = unchanged): when multiple
        tickers signal entry on the same day and there are fewer open
        position slots than candidates, fill slots by descending
        volume_ratio (today's volume vs its 20d average — see
        halal_bot.signals.strategy) instead of alphabetical ticker order.
        Only changes *which* already-qualified signals get taken on a
        collision day, not the entry/exit signal logic itself.

        Backtested and REJECTED on the same 3-year train/test split used for
        macd_filter/adx_filter — ranked lost on CAGR/Sharpe/max-drawdown/
        win-rate in both the full sample and the train split (e.g. full-
        sample Sharpe 1.52 -> 1.27, win rate 66.7% -> 62.0%), and was an
        exact no-op on the held-out test split (that year never had a
        collision day — never more same-day signals than open slots — so
        ranking had nothing to reorder). 0/12 metric-window checks favored
        it. Likely cause: every candidate has already cleared the same four
        entry gates, so reordering them adds no information — and the
        biggest one-day volume_ratio spikes skew toward news-driven gap
        events, which are more likely to mean-revert than a clean breakout
        is. Kept as a documented dead end, same status as adx_filter/
        vol_sizing/trailing_stop — not a live option, default off.

        rank_entries_by_macd (opt-in, default False = unchanged): same
        collision-day mechanism as rank_entries, but ranks candidates by
        descending macd_strength (macd_hist / Close, see
        halal_bot.signals.strategy) instead of volume_ratio. Mutually
        exclusive with rank_entries in effect: if both are True, this one
        wins.

        Backtested and REJECTED on the same split — only won 2/12
        metric-window checks (CAGR/Sharpe on the held-out test split alone),
        and lost badly everywhere else: train-split Sharpe roughly halved
        (1.26 -> 0.81), and max drawdown got worse in every window
        (full -17.2% -> -20.7%, test -5.4% -> -6.7%). A single test-split
        win with in-sample (train) performance this much worse isn't
        adoptable by this project's bar (full+train+test, like macd_filter
        cleared). Likely cause: ranking by *how far* MACD has already
        separated favors tickers already deep into a momentum run over
        fresher breakouts — buying the most extended mover among today's
        crossers courts exactly the sharper pullback the worse drawdown
        numbers show, and it only paid off in the test window's strongly
        trending stretch. Kept as a documented dead end, not a live
        option, default off.

        min_hold_days (opt-in, default 0 = unchanged): suppresses the
        signal exit (death cross) until a position has been held at least
        this many *calendar* days — stop-loss and profit-take in
        halal_bot.risk are untouched by this, on purpose: capital
        protection should never wait on a hold-period rule, only the
        trend-following exit does. Hypothesis was that a death cross
        firing within days of entry is more likely ordinary noise than a
        real reversal, and exiting into it churns a position too early.

        Backtested and REJECTED on the same 3-year train/test split
        (2/3-1/3), swept over 3/5/7/10-day holds: max drawdown got WORSE
        at every hold length in every window (full -17.2% -> -17.5% to
        -20.5%) -- same failure mode as trailing_stop, delaying the exit
        just rides out more downside before finally selling. Train-split
        (2yr, in-sample) results lost on every metric at every hold length
        tested, no exceptions. The apparent test-split (1yr, held-out)
        improvement in CAGR/Sharpe/win-rate was identical between the
        5-day and 7-day variants -- a strong sign it's a couple of
        specific trades dodging one bad quick-exit in that one year, not a
        repeatable effect. Best case (5 or 7 days) cleared only 6/12
        checks and lost on drawdown regardless. Kept as a documented dead
        end, same status as adx_filter/vol_sizing/trailing_stop/
        rank_entries — not a live option, default off."""
        self.sector_map = sector_map
        self.vol_sizing = vol_sizing
        self.trailing_stop = trailing_stop
        self.monthly_contribution = monthly_contribution
        self.rank_entries = rank_entries
        self.rank_entries_by_macd = rank_entries_by_macd
        self.min_hold_days = min_hold_days
        self.signals = {t: generate_signals(df, adx_filter=adx_filter, macd_filter=macd_filter)
                         for t, df in price_data.items() if not df.empty}
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

        self.volume_ratio_native: dict[str, pd.Series] = {}
        if rank_entries:
            self.volume_ratio_native = {
                t: df["volume_ratio"].reindex(self.master_dates, fill_value=0.0)
                for t, df in self.signals.items()
            }

        self.macd_strength_native: dict[str, pd.Series] = {}
        if rank_entries_by_macd:
            self.macd_strength_native = {
                t: df["macd_strength"].reindex(self.master_dates, fill_value=0.0)
                for t, df in self.signals.items()
            }

        self.atr_pct_ff: dict[str, pd.Series] = {}
        if vol_sizing or trailing_stop:
            period = CONFIG.risk.atr_period
            for t, df in price_data.items():
                if t not in self.signals or len(df) <= period:
                    continue
                series = compute_atr_pct(df["High"], df["Low"], df["Close"], period)
                self.atr_pct_ff[t] = series.reindex(self.master_dates).ffill()

    def _size_multiplier(self, ticker: str, date) -> float:
        if not self.vol_sizing:
            return 1.0
        atr = self.atr_pct_ff[ticker].get(date) if ticker in self.atr_pct_ff else None
        return volatility_size_multiplier(atr)

    def _prices_on(self, date) -> dict[str, float]:
        """Forward-filled close for every ticker with a valid price by this
        date. Deliberately does NOT require *native* (non-ffilled) data on
        this exact date — a ticker with a one-day data gap from the provider
        (rare, but real: e.g. SPUS/HLAL/ABBV/PSX all show a gap on one
        specific date in yfinance's feed as of this writing) should carry
        forward its last known price for valuation, not silently drop to $0
        for that day. A previous version required native data and produced
        exactly that bug: a ~20% single-day equity cliff that fully
        "recovered" the next day, entirely a valuation artifact, not a real
        market move. Entry/exit *signals* are unaffected by this — those
        still only fire on days with genuine native data (see entry_native/
        exit_native above, reindexed with fill_value=False)."""
        return {
            t: self.close_ff[t][date]
            for t in self.signals
            if not pd.isna(self.close_ff[t][date])
        }

    def run(self, start_date=None, end_date=None, starting_capital: float | None = None) -> BacktestResult:
        """start_date/end_date (optional): restrict simulated trading + equity
        tracking to this date range, while indicators still see the full
        price history for warm-up (SMA/RSI are backward-looking only, so
        this introduces no lookahead). Used for train/test out-of-sample
        splits without re-fetching or re-warming indicators per split.
        starting_capital (optional): overrides CONFIG.backtest.starting_capital.
        """
        cfg = CONFIG.backtest
        slippage = cfg.slippage_bps / 10_000

        portfolio = PortfolioState(cash=starting_capital if starting_capital is not None else cfg.starting_capital)
        trades: list[Trade] = []
        equity_points: list[tuple] = []
        total_contributed = 0.0
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
                pos.update_high(price)
                atr = self.atr_pct_ff[ticker].get(date) if ticker in self.atr_pct_ff else None
                decision = check_exit_risk(pos, price, trailing_stop=self.trailing_stop, atr_pct=atr)

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

                # Anchor ETFs don't get sold on a death cross — they're
                # supposed to be held independent of the technical signal
                # (that's the whole point of an "anchor"). Without this,
                # a death cross sells it and the anchor-allocation block
                # below immediately rebuys it same-day, churning slippage
                # for no purpose on every whipsaw.
                is_anchor = ticker in CONFIG.portfolio.anchor_etf_tickers[
                    :CONFIG.portfolio.anchor_etf_slots
                ]
                held_days = (
                    (date.date() - pd.Timestamp(pos.entry_date).date()).days
                    if pos.entry_date else 10**6
                )
                if (not is_anchor and ticker in self.exit_native
                        and bool(self.exit_native[ticker].get(date, False))
                        and held_days >= self.min_hold_days):
                    sell_price = price * (1 - slippage)
                    pnl = (sell_price - pos.entry_price) * pos.shares
                    portfolio.cash += pos.shares * sell_price
                    trades.append(Trade(str(date.date()), ticker, "signal_exit", pos.shares,
                                         sell_price, pnl, self.reason_native[ticker].get(date, "")))
                    del portfolio.positions[ticker]

            # --- Monthly contribution (opt-in, default 0.0): deposited cash,
            # same cadence as the rebalance check below. Recorded separately
            # so equity growth from deposits doesn't get read as trading gain.
            if self.monthly_contribution and i % REBALANCE_INTERVAL_TRADING_DAYS == 0 and i > 0:
                portfolio.cash += self.monthly_contribution
                total_contributed += self.monthly_contribution
                trades.append(Trade(str(date.date()), "CASH", "contribution", 0, 0,
                                     reason=f"Monthly contribution +${self.monthly_contribution:,.0f}"))

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

            # --- Anchor allocation (SPEC.md Section 3): force-hold configured
            # anchor ETFs at the standard position size, independent of the
            # technical signal — a stability anchor that depends on catching
            # a golden cross isn't actually an anchor. Runs before the
            # signal-driven entries below so anchors get first claim on open
            # slots. Re-buys automatically if ever stopped out, since
            # `ticker in portfolio.positions` is the only gate.
            equity = portfolio.equity(prices)
            for ticker in CONFIG.portfolio.anchor_etf_tickers[:CONFIG.portfolio.anchor_etf_slots]:
                if ticker not in self.signals or ticker in portfolio.positions or ticker not in prices:
                    continue
                price = prices[ticker]
                sector = self.sector_map.get(ticker, "Unknown")
                dollars = min(max_position_dollars(equity), portfolio.cash)
                allowed, _reason = can_open_new_position(portfolio, sector, dollars, prices)
                if not allowed:
                    continue
                buy_price = price * (1 + slippage)
                size_mult = self._size_multiplier(ticker, date)
                shares = position_size_shares(equity, buy_price, size_multiplier=size_mult)
                cost = shares * buy_price
                if shares <= 0 or cost > portfolio.cash:
                    continue
                portfolio.cash -= cost
                portfolio.positions[ticker] = Position(
                    ticker=ticker, sector=sector, shares=shares,
                    entry_price=buy_price, entry_date=str(date.date()),
                )
                trades.append(Trade(str(date.date()), ticker, "buy", shares, buy_price,
                                     reason="Anchor allocation (forced, independent of signal)"))

            # --- Entries ---
            equity = portfolio.equity(prices)
            candidates = [
                t for t in self.signals
                if t not in portfolio.positions and t in prices
                and bool(self.entry_native[t].get(date, False))
            ]
            if self.rank_entries_by_macd:
                candidates.sort(key=lambda t: self.macd_strength_native[t].get(date, 0.0),
                                 reverse=True)
            elif self.rank_entries:
                # Most volume conviction first, so a collision day (more
                # signals than open slots) fills with the strongest setups
                # rather than whichever ticker sorts first alphabetically.
                candidates.sort(key=lambda t: self.volume_ratio_native[t].get(date, 0.0),
                                 reverse=True)
            else:
                candidates.sort()

            for ticker in candidates:
                price = prices[ticker]
                sector = self.sector_map.get(ticker, "Unknown")
                dollars = min(max_position_dollars(equity), portfolio.cash)
                allowed, _reason = can_open_new_position(portfolio, sector, dollars, prices)
                if not allowed:
                    continue
                buy_price = price * (1 + slippage)
                size_mult = self._size_multiplier(ticker, date)
                shares = position_size_shares(equity, buy_price, size_multiplier=size_mult)
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

        return BacktestResult(equity_curve=equity_curve, trades=trades, metrics=metrics,
                               total_contributed=total_contributed)
