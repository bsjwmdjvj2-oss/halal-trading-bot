# Halal Growth Trading Agent Bot

Rules-based, halal-screened growth portfolio bot for Alpaca (paper trading first).
See `SPEC.md` (the original build spec) for full requirements.

## Build order

1. ✅ Project scaffold
2. ✅ Halal watchlist (`data/watchlist.yaml`)
3. ✅ AAOIFI-style screening (`halal_bot/screening/`)
4. ✅ Technical signal generation (`halal_bot/signals/`) — golden cross + RSI + volume +
   MACD confirmation, tuned and out-of-sample validated (an ADX trend filter was also
   tested and rejected — see commit history / module docstring in `strategy.py`)
5. ✅ Risk management rules (`halal_bot/risk/`)
6. ✅ Backtest engine + metrics (`halal_bot/backtest/`)
7. ✅ Audit-trail logging (`halal_bot/logging_utils.py`)
8. ✅ Live daily trading job (`halal_bot/live/`), Alpaca client, Telegram bot, deployed per `DEPLOY.md`
9. ✅ AI summary agent (`halal_bot/ai_summary/`) — narrates the daily Telegram summary when
   `ANTHROPIC_API_KEY` is set; degrades silently to a deterministic summary without one
10. ✅ P&L dashboard (`halal_bot/dashboard/`) — win rate, max drawdown, Sharpe, daily/weekly/
    monthly P&L from real trade history, via `scripts/generate_dashboard.py` or `/dashboard`
11. ✅ Quarterly research agent (`halal_bot/research/`, SPEC.md Section 6) — Claude +
    web search proposes new watchlist candidates and flags qualitative halal-compliance
    concerns the ratio screen can't catch. Advisory only — never edits `watchlist.yaml`.
    Runs via `scripts/run_quarterly_research.py`, needs `ANTHROPIC_API_KEY`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in Alpaca + Telegram credentials
```

## Run a backtest

```bash
python scripts/run_backtest.py
```

Outputs an equity curve, trade log, and metrics (win rate, max drawdown,
Sharpe-like ratio) to `backtest_results/`.

## Run the live daily job locally (dry run by default)

```bash
python scripts/run_daily.py
```

Defaults to a dry run — logs what it would trade, submits nothing — until
`LIVE_TRADING_ENABLED=true` is set in `.env`. See `DEPLOY.md` for the full
PythonAnywhere deployment (daily Scheduled Task + always-on Telegram bot).

## Important caveats

- **Halal screening is not a substitute for professional Shariah advisory.**
  The screening module applies a maintained, internal AAOIFI-style
  rule set (debt ratio, impure income ratio, cash/securities ratio, sector
  exclusion) against free fundamentals data. Verify compliance independently
  before committing real capital.
- **Paper trading only** until Section 13 of the spec's criteria are met
  (2-3+ months of paper results reviewed against dashboard metrics, not raw P&L).
- This bot does not provide personalized investment advice.
