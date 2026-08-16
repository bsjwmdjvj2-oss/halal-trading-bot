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


def position_size_shares(equity: float, price: float) -> float:
    """Whole shares affordable within the max-position-size cap."""
    if price <= 0:
        return 0.0
    dollars = max_position_dollars(equity)
    return float(int(dollars // price))


def check_exit_risk(position: Position, current_price: float) -> RiskDecision:
    """Stop-loss and profit-taking checks for a single position.

    Stop-loss takes priority if both would somehow trigger (shouldn't happen
    since one is a loss and the other a gain, but stop-loss is the safety
    rule so it wins any ambiguity).
    """
    cfg = CONFIG.risk
    ret = position.unrealized_return(current_price)

    if ret <= -cfg.stop_loss_pct:
        return RiskDecision(
            action="stop_loss",
            shares=position.shares,
            reason=f"Unrealized return {ret:.1%} breached stop-loss {-cfg.stop_loss_pct:.0%}",
        )

    if ret >= cfg.profit_take_trigger_pct and not position.scaled_out:
        scale_shares = float(int(position.shares * cfg.profit_take_scale_out_pct))
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
        pos.market_value(prices[t])
        for t, pos in portfolio.positions.items()
        if pos.sector == sector and t in prices
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
    if len(portfolio.positions) >= CONFIG.portfolio.target_positions_max:
        return False, (
            f"At max concurrent positions ({CONFIG.portfolio.target_positions_max})"
        )
    if not sector_cap_allows(portfolio, sector, dollars, prices):
        return False, (
            f"Sector '{sector}' would exceed "
            f"{CONFIG.risk.max_sector_concentration_pct:.0%} concentration cap"
        )
    if dollars > portfolio.cash:
        return False, "Insufficient cash"
    return True, "OK"
