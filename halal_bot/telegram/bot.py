"""Telegram interface (SPEC.md Section 12).

Runs as its own always-on process (PythonAnywhere Always-on Task), separate
from the daily trading job (Scheduled Task) — so /pause and /resume can't
just flip an in-memory flag, they have to go through the same on-disk
LiveState the daily job reads (halal_bot.live.state_store). That's what
makes /pause an effective kill-switch across process restarts.
"""
from __future__ import annotations

import asyncio
import os

from halal_bot.config import CONFIG
from halal_bot.live.state_store import load_state, set_paused
from halal_bot.logging_utils import log_event


class TradingStateFlag:
    """Thin wrapper over the persistent LiveState pause flag (process-safe via the JSON store)."""

    def pause(self) -> None:
        set_paused(True)
        log_event("manual_pause", "Trading paused via /pause command")

    def resume(self) -> None:
        set_paused(False)
        log_event("manual_resume", "Trading resumed via /resume command")

    @property
    def is_paused(self) -> bool:
        return load_state().trading_paused


TRADING_STATE = TradingStateFlag()


def default_status_fn() -> str:
    """Default /status text: live Alpaca account snapshot + open positions.

    Plain text, no Telegram parse_mode — trade/signal reasons elsewhere in
    the bot contain "<"/">" (e.g. "rsi=64.2 < 70.0") which would break
    HTML/Markdown parsing and silently drop the message. Emojis + layout
    give a real readability upgrade without that risk.
    """
    from halal_bot.broker.alpaca_client import AlpacaClient
    from halal_bot.risk.rules import max_positions_for_equity

    account = AlpacaClient().get_account_snapshot()
    state = load_state()

    lines = [
        "📊 PORTFOLIO STATUS",
        "",
        f"💰 Equity:  ${account.equity:,.2f}",
        f"💵 Cash:    ${account.cash:,.2f}",
        f"{'⏸️ Paused:  YES' if state.trading_paused else '▶️ Paused:  no'}",
        "",
        f"📈 Positions ({len(account.positions)}/{max_positions_for_equity(account.equity)})",
    ]
    if account.positions:
        for ticker, p in sorted(account.positions.items()):
            cost_basis = p["qty"] * p["avg_entry_price"]
            unrealized = p["market_value"] - cost_basis
            pct = (unrealized / cost_basis * 100) if cost_basis else 0.0
            arrow = "🟢" if unrealized >= 0 else "🔴"
            lines.append(
                f"{arrow} {ticker}: {p['qty']:.2f} sh · ${p['market_value']:,.2f} "
                f"· {unrealized:+,.2f} ({pct:+.1f}%)"
            )
    else:
        lines.append("  (none)")
    return "\n".join(lines)


def _require_config():
    if not CONFIG.telegram.bot_token or not CONFIG.telegram.chat_id:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — fill in .env before using the bot"
        )


async def send_alert(message: str) -> None:
    """Fire-and-forget alert send — trade entries/exits, drawdown pause, summaries."""
    _require_config()
    from telegram import Bot

    bot = Bot(token=CONFIG.telegram.bot_token)
    await bot.send_message(chat_id=CONFIG.telegram.chat_id, text=message)


def build_application(portfolio_status_fn=None):
    """portfolio_status_fn: callable returning a plain-English status string (holdings + P&L).
    Defaults to `default_status_fn` (live Alpaca snapshot) if not supplied.

    Every command is restricted to CONFIG.telegram.chat_id — anyone else who
    finds the bot can't pause/resume trading or read portfolio state.
    """
    _require_config()
    portfolio_status_fn = portfolio_status_fn or default_status_fn
    from telegram import BotCommand, Update
    from telegram.ext import Application, CommandHandler, ContextTypes

    def _authorized(update: Update) -> bool:
        incoming = str(update.effective_chat.id)
        configured = str(CONFIG.telegram.chat_id)
        ok = incoming == configured
        print(f"[telegram] incoming message from chat_id={incoming} "
              f"(configured chat_id={configured}) -> {'authorized' if ok else 'IGNORED, mismatch'}")
        return ok

    async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        print("[telegram] /status received")
        if not _authorized(update):
            return
        await update.message.reply_text(portfolio_status_fn())

    async def pause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        print("[telegram] /pause received")
        if not _authorized(update):
            return
        TRADING_STATE.pause()
        await update.message.reply_text(
            "⏸️ Trading paused. No new positions will be opened until /resume."
        )

    async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        print("[telegram] /resume received")
        if not _authorized(update):
            return
        TRADING_STATE.resume()
        await update.message.reply_text("▶️ Trading resumed.")

    invest_lock = asyncio.Lock()

    async def invest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """On-demand trigger for the exact same run_once() the scheduled
        daily job calls — same signal generation, same risk/position-sizing/
        sector-cap/halal-screen checks, same LIVE_TRADING_ENABLED gate. This
        is a different *trigger* for that one code path, not a different or
        looser way to buy: it doesn't bypass any of the existing checks, and
        the double-buy guard (open orders checked before every entry) is the
        same protection that already covers two overlapping runs, so this is
        safe to invoke even while the scheduled run is close to firing."""
        print("[telegram] /invest received")
        if not _authorized(update):
            return
        if invest_lock.locked():
            await update.message.reply_text("⏳ Already running — try again once it finishes.")
            return

        async with invest_lock:
            live = os.getenv("LIVE_TRADING_ENABLED", "false").strip().lower() == "true"
            await update.message.reply_text(
                "🔄 Running now — " + ("LIVE, real orders will submit..." if live
                                        else "DRY RUN (LIVE_TRADING_ENABLED=false)...")
            )
            from halal_bot.broker.alpaca_client import AlpacaNotConfiguredError
            from halal_bot.live.daily_runner import run_once

            try:
                # Runs in a worker thread: run_once() is blocking (yfinance +
                # Alpaca calls across the whole watchlist) and internally
                # calls asyncio.run() for its own Telegram send when live —
                # both need to happen off this bot's own event loop.
                summary = await asyncio.to_thread(run_once, live=live)
            except AlpacaNotConfiguredError as e:
                await update.message.reply_text(f"⚠️ CONFIG ERROR: {e}")
                return
            except Exception as e:
                log_event("invest_cmd_crashed", str(e))
                await update.message.reply_text(f"⚠️ Run failed: {e}")
                return

            for i in range(0, len(summary), 4000):
                await update.message.reply_text(summary[i:i + 4000])

    dca_lock = asyncio.Lock()

    async def dca_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Reference calculator only — never touches the live bot's own
        position sizing (still 15%-of-equity, signal-triggered, unchanged).
        Usage: /dca (uses current Alpaca cash balance) or /dca 500 (manual
        override, e.g. to plan a hypothetical future contribution)."""
        print("[telegram] /dca received")
        if not _authorized(update):
            return
        amount: float
        if context.args:
            raw = context.args[0].replace("$", "").replace(",", "")
            try:
                amount = float(raw)
            except ValueError:
                await update.message.reply_text(f"Couldn't parse {context.args[0]!r} as a dollar amount.")
                return
            if amount <= 0:
                await update.message.reply_text("Amount must be positive.")
                return
        else:
            from halal_bot.broker.alpaca_client import AlpacaClient, AlpacaNotConfiguredError
            try:
                account = AlpacaClient().get_account_snapshot()
            except AlpacaNotConfiguredError as e:
                await update.message.reply_text(f"⚠️ CONFIG ERROR: {e}")
                return
            amount = account.cash
            await update.message.reply_text(f"Using your current Alpaca cash balance: ${amount:,.2f}")

        if dca_lock.locked():
            await update.message.reply_text("⏳ Already running — try again once it finishes.")
            return

        async with dca_lock:
            from halal_bot.live.state_store import load_state
            from halal_bot.research.dca_calculator import pick_candidates, stocks_for_amount

            state = load_state()
            if not state.compliant_universe:
                await update.message.reply_text(
                    "No compliant universe cached yet — the bot needs to run its monthly "
                    "re-screen at least once before /dca has anything to pick from."
                )
                return

            count = stocks_for_amount(amount)
            await update.message.reply_text(
                f"💵 ${amount:,.0f} this month → {count} stocks. Checking today's signals..."
            )
            try:
                picked, total_signaling = await asyncio.to_thread(pick_candidates, count, state)
            except Exception as e:
                log_event("dca_cmd_crashed", str(e))
                await update.message.reply_text(f"⚠️ Run failed: {e}")
                return

            if not picked:
                await update.message.reply_text(
                    "No tickers have an active entry signal today — nothing to suggest "
                    "right now. This is a planning tool, not a trade; it's fine for it to "
                    "come back empty on a quiet day."
                )
                return

            lines = [f"📋 {len(picked)} of {count} requested (spread across sectors):"]
            for c in picked:
                lines.append(f"  {c.ticker} ({c.sector}) — {c.reason}")
            if len(picked) < count:
                lines.append(
                    f"\nOnly {total_signaling} ticker(s) have an active signal today, "
                    f"fewer than the {count} the table suggests — showing what's real "
                    "rather than padding the list."
                )
            text = "\n".join(lines)
            for i in range(0, len(text), 4000):
                await update.message.reply_text(text[i:i + 4000])

    async def dashboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        print("[telegram] /dashboard received")
        if not _authorized(update):
            return
        from halal_bot.dashboard.report import build_dashboard, render_text as render_dashboard

        await update.message.reply_text(render_dashboard(build_dashboard()))

    async def screening_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        print("[telegram] /screening received")
        if not _authorized(update):
            return
        from halal_bot.screening.report import load_latest_screening, render_text as render_screening

        text = render_screening(load_latest_screening())

        # TipRanks section, if a snapshot exists (see tipranks_context's module
        # docstring: TipRanks is only reachable via a chat-session MCP
        # connection, so this reads whatever was last pulled there, not a
        # live call -- silently omitted when there's no snapshot yet.
        from halal_bot.research.tipranks_context import format_telegram_screening, load_snapshot
        from halal_bot.screening.watchlist import load_watchlist

        snapshot = load_snapshot()
        if snapshot is not None:
            tipranks_text = format_telegram_screening(snapshot, load_watchlist())
            if tipranks_text:
                text = text + "\n\n" + tipranks_text

        # Telegram caps messages at 4096 chars — the compliant-ticker list can
        # approach that with a large watchlist, so split rather than truncate.
        for i in range(0, len(text), 4000):
            await update.message.reply_text(text[i:i + 4000])

    async def _register_command_menu(application: Application) -> None:
        # Populates Telegram's native "/" menu button in the chat — otherwise
        # commands only work if you already know and type them from memory.
        await application.bot.set_my_commands([
            BotCommand("status", "Portfolio status & open positions"),
            BotCommand("invest", "Run entries now (cash -> today's picks)"),
            BotCommand("dca", "DCA calculator: cash balance -> stocks to buy"),
            BotCommand("dashboard", "P&L, win rate, drawdown, Sharpe"),
            BotCommand("screening", "Halal screen: who passed/failed & why"),
            BotCommand("pause", "Stop opening new positions"),
            BotCommand("resume", "Resume trading"),
        ])

    application = (
        Application.builder()
        .token(CONFIG.telegram.bot_token)
        .post_init(_register_command_menu)
        .build()
    )
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("invest", invest_cmd))
    application.add_handler(CommandHandler("dca", dca_cmd))
    application.add_handler(CommandHandler("dashboard", dashboard_cmd))
    application.add_handler(CommandHandler("screening", screening_cmd))
    application.add_handler(CommandHandler("pause", pause_cmd))
    application.add_handler(CommandHandler("resume", resume_cmd))
    return application


def run_bot(portfolio_status_fn=None) -> None:
    """Blocking call — run in its own always-on process (see scripts/run_telegram_bot.py)."""
    app = build_application(portfolio_status_fn)
    app.run_polling()
