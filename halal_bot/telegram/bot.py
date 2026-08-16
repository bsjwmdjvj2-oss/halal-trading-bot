"""Telegram interface (SPEC.md Section 12).

Runs as its own always-on process (PythonAnywhere Always-on Task), separate
from the daily trading job (Scheduled Task) — so /pause and /resume can't
just flip an in-memory flag, they have to go through the same on-disk
LiveState the daily job reads (halal_bot.live.state_store). That's what
makes /pause an effective kill-switch across process restarts.
"""
from __future__ import annotations

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
    from halal_bot.config import CONFIG
    from halal_bot.broker.alpaca_client import AlpacaClient

    account = AlpacaClient().get_account_snapshot()
    state = load_state()

    lines = [
        "📊 PORTFOLIO STATUS",
        "",
        f"💰 Equity:  ${account.equity:,.2f}",
        f"💵 Cash:    ${account.cash:,.2f}",
        f"{'⏸️ Paused:  YES' if state.trading_paused else '▶️ Paused:  no'}",
        "",
        f"📈 Positions ({len(account.positions)}/{CONFIG.portfolio.target_positions_max})",
    ]
    if account.positions:
        for ticker, p in sorted(account.positions.items()):
            cost_basis = p["qty"] * p["avg_entry_price"]
            unrealized = p["market_value"] - cost_basis
            pct = (unrealized / cost_basis * 100) if cost_basis else 0.0
            arrow = "🟢" if unrealized >= 0 else "🔴"
            lines.append(
                f"{arrow} {ticker}: {p['qty']:g} sh · ${p['market_value']:,.2f} "
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
        # Telegram caps messages at 4096 chars — the compliant-ticker list can
        # approach that with a large watchlist, so split rather than truncate.
        for i in range(0, len(text), 4000):
            await update.message.reply_text(text[i:i + 4000])

    async def _register_command_menu(application: Application) -> None:
        # Populates Telegram's native "/" menu button in the chat — otherwise
        # commands only work if you already know and type them from memory.
        await application.bot.set_my_commands([
            BotCommand("status", "Portfolio status & open positions"),
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
    application.add_handler(CommandHandler("dashboard", dashboard_cmd))
    application.add_handler(CommandHandler("screening", screening_cmd))
    application.add_handler(CommandHandler("pause", pause_cmd))
    application.add_handler(CommandHandler("resume", resume_cmd))
    return application


def run_bot(portfolio_status_fn=None) -> None:
    """Blocking call — run in its own always-on process (see scripts/run_telegram_bot.py)."""
    app = build_application(portfolio_status_fn)
    app.run_polling()
