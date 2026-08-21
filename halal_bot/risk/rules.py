"""Risk management rules (SPEC.md Section 5).

Pure functions over PortfolioState so both the backtester and (later) the
live bot exercise the exact same logic — the whole point of backtesting
first is that these rules don't change between the two.
"""
from __future__ import annotations

from dataclasses import dataclass

from halal_bot.config import CONFIG
from halal_bot.portfolio import Position, PortfolioState


@dataclass
class RiskDecision:
    action: str          # "stop_loss" | "scale_out" | "none"
    shares: float = 0.0
    reason: str = ""


def max_position_dollars(equity: float) -> float:
    return equity * CONFIG.risk.max_position_size_pct


def max_positions_for_equity(equity: float) -> int:
    """Concurrent-position cap, scaled down for small accounts so they
    aren't spread across more simultaneous slices than their equity can
    meaningfully support -- see PortfolioConfig.min_position_dollars."""
    cfg = CONFIG.portfolio
    scaled = int(equity // cfg.min_position_dollars)
    return max(1, min(cfg.target_positions_max, scaled))


def position_size_shares(equity: float, price: float, size_multiplier: float = 1.0) -> float:
    """Fractional shares affordable within the max-position-size cap (Alpaca
    supports fractional-share market orders; rounded to 6 decimal places,
    matching Alpaca's own fractional precision, to avoid floating-point
    noise). Whole-share-only sizing used to mean a $300 account rounded down
    to 0 shares for anything pricier than the ~$45 cap (e.g. SPUS at $58,
    HLAL at $72) -- the forced anchor-ETF buy would silently never fire.

    size_multiplier (opt-in, default 1.0 = unchanged): scales the dollar cap
    down for volatility-based sizing (see volatility_size_multiplier) — never
    used to size a position larger than the flat max_position_size_pct cap.
    """
    if price <= 0:
        return 0.0
    dollars = max_position_dollars(equity) * min(size_multiplier, 1.0)
    return round(dollars / price, 6)


def volatility_size_multiplier(atr_pct: float | None) -> float:
    """Scales the normal position-size cap down for tickers whose recent
    ATR% (average daily range as a fraction of price) is above the
    configured "normal" reference level — shakier names get smaller bets.
    Never scales the cap up (capped at 1.0) and never below
    vol_sizing_floor_mult, so this only ever shrinks the existing
    max_position_size_pct ceiling, it can't create a new way to exceed it.
    Returns 1.0 (no adjustment) if atr_pct is missing/non-positive.

    Backtested and REJECTED (BacktestEngine(vol_sizing=True)) — lost on
    CAGR/Sharpe/win-rate in every train/test window because it systematically
    underweights the volatile growth names this strategy depends on for
    return. Kept as a documented dead end, not a live option."""
    cfg = CONFIG.risk
    if not atr_pct or atr_pct <= 0:
        return 1.0
    return min(1.0, max(cfg.vol_sizing_floor_mult, cfg.vol_sizing_reference_atr_pct / atr_pct))


def check_exit_risk(
    position: Position, current_price: float,
    trailing_stop: bool = False, atr_pct: float | None = None,
) -> RiskDecision:
    """Stop-loss and profit-taking checks for a single position.

    Stop-loss takes priority if both would somehow trigger (shouldn't happen
    since one is a loss and the other a gain, but stop-loss is the safety
    rule so it wins any ambiguity).

    trailing_stop (opt-in, default False): replaces the fixed
    entry-anchored stop-loss with an ATR-scaled trail off the position's
    peak price since entry (position.highest_price). Falls back to the
    fixed stop-loss if atr_pct is unavailable that day.

    Backtested and REJECTED (BacktestEngine(trailing_stop=True)) — lost on
    CAGR/Sharpe/max-drawdown/win-rate in every train/test window. It cuts
    trend-followers short on ordinary pullbacks well before the death-cross
    exit would fire, and gets whipsawed hardest during the same broad
    pullbacks it was meant to protect against — max drawdown got WORSE, not
    better. Kept as a documented dead end, not a live option.
    """
    cfg = CONFIG.risk
    ret = position.unrealized_return(current_price)

    if trailing_stop and atr_pct and atr_pct > 0:
        trail_pct = min(
            cfg.trailing_stop_ceiling_pct,
            max(cfg.trailing_stop_floor_pct, atr_pct * cfg.trailing_stop_atr_multiple),
        )
        drop_from_peak = (current_price - position.highest_price) / position.highest_price
        if drop_from_peak <= -trail_pct:
            return RiskDecision(
                action="stop_loss",
                shares=position.shares,
                reason=(
                    f"Trailing stop: {drop_from_peak:.1%} off peak ${position.highest_price:.2f} "
                    f"(ATR-scaled trail {trail_pct:.1%})"
                ),
            )
    elif ret <= -cfg.stop_loss_pct:
        return RiskDecision(
            action="stop_loss",
            shares=position.shares,
            reason=f"Unrealized return {ret:.1%} breached stop-loss {-cfg.stop_loss_pct:.0%}",
        )

    if ret >= cfg.profit_take_trigger_pct and not position.scaled_out:
        scale_shares = round(position.shares * cfg.profit_take_scale_out_pct, 6)
        if scale_shares > 0:
            return RiskDecision(
                action="scale_out",
                shares=scale_shares,
                reason=(
                    f"Unrealized return {ret:.1%} crossed profit-take trigger "
                    f"{cfg.profit_take_trigger_pct:.0%}; scaling out "
                    f"{cfg.profit_take_scale_out_pct:.0%} of position"
                ),
            )

    return RiskDecision(action="none")


def check_drawdown_pause(equity: float, equity_peak: float) -> bool:
    """True if new position entries should be paused (Section 5).

    No auto-liquidation — this only blocks new entries and is surfaced via
    Telegram alert by the caller.
    """
    if equity_peak <= 0:
        return False
    drawdown = (equity_peak - equity) / equity_peak
    return drawdown >= CONFIG.risk.drawdown_pause_pct


def sector_cap_allows(
    portfolio: PortfolioState, sector: str, additional_dollars: float, prices: dict[str, float]
) -> bool:
    equity = portfolio.equity(prices)
    if equity <= 0:
        return True
    projected_equity = equity + additional_dollars if additional_dollars > 0 else equity
    current_sector_value = sum(
        pos.market_value(prices.get(t, pos.entry_price))
        for t, pos in portfolio.positions.items()
        if pos.sector == sector
    )
    projected_sector_value = current_sector_value + additional_dollars
    projected_ratio = projected_sector_value / projected_equity
    return projected_ratio <= CONFIG.risk.max_sector_concentration_pct


def can_open_new_position(
    portfolio: PortfolioState,
    sector: str,
    dollars: float,
    prices: dict[str, float],
) -> tuple[bool, str]:
    if portfolio.trading_paused:
        return False, "Trading paused (drawdown threshold breached)"
    if check_drawdown_pause(portfolio.equity(prices), portfolio.equity_peak):
        return False, "Portfolio drawdown pause active"
    max_positions = max_positions_for_equity(portfolio.equity(prices))
    if len(portfolio.positions) >= max_positions:
        return False, f"At max concurrent positions ({max_positions})"
    if not sector_cap_allows(portfolio, sector, dollars, prices):
        return False, (
            f"Sector '{sector}' would exceed "
            f"{CONFIG.risk.max_sector_concentration_pct:.0%} concentration cap"
        )
    if dollars > portfolio.cash:
        return False, "Insufficient cash"
    return True, "OK"
