# Deploying to PythonAnywhere

Two long-running pieces, run as two separate PythonAnywhere tasks:

| Piece | PythonAnywhere feature | Schedule |
|---|---|---|
| `scripts/run_daily.py` | **Scheduled Task** | Once/day, after US market close |
| `scripts/run_telegram_bot.py` | **Always-on Task** | Continuous |

Both require the paid **Hacker tier or above** — the free tier has no
always-on tasks and blocks outbound requests to non-whitelisted hosts,
which breaks both Alpaca and `api.telegram.org` (SPEC.md Section 2/12).

## 1. Get the code onto PythonAnywhere

This repo has no GitHub remote yet. Easiest path:

```bash
# locally, from the project root
gh repo create halal-trading-bot --private --source=. --remote=origin
git add -A && git commit -m "Initial commit"
git push -u origin master
```

Then, in a PythonAnywhere **Bash console**:

```bash
git clone https://github.com/<you>/halal-trading-bot.git TradingAlpaca
cd TradingAlpaca
```

(No GitHub? Zip the project locally and upload it via the **Files** tab,
then `unzip` it in a Bash console instead.)

## 2. Set up the environment

In the same Bash console:

```bash
cd ~/TradingAlpaca
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Use whichever Python 3.x is available on your plan (check with `python3 --version`
in the console, or the **Consoles** tab dropdown).

## 3. Create `.env` on the server

`.env` is gitignored on purpose — create it directly on PythonAnywhere via
the **Files** tab (New file → `.env` inside `TradingAlpaca/`) or `nano .env`
in the console. Fill in:

```
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets

TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

LIVE_TRADING_ENABLED=false
```

- **Alpaca keys**: from your Alpaca **paper** account dashboard (Section 2 — paper only, not live).
- **Telegram bot token**: message [@BotFather](https://t.me/BotFather) → `/newbot`.
- **Telegram chat ID**: message your new bot once, then visit
  `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and read
  `message.chat.id` from the JSON — or just ask [@userinfobot](https://t.me/userinfobot).
- Leave `LIVE_TRADING_ENABLED=false` for now — see step 6.

## 4. Test manually before scheduling anything

Still in the Bash console, with the venv active:

```bash
python scripts/run_daily.py
```

This runs one dry-run pass: screens the universe, checks signals, prints
exactly what it *would* trade, and sends nothing to Alpaca or Telegram
(dry run never sends alerts either — see `halal_bot/live/daily_runner.py`).
Confirm the output looks sane before moving on.

Then test the Telegram side:

```bash
python scripts/run_telegram_bot.py
```

Message your bot `/status` from Telegram — you should get a live Alpaca
account snapshot back. `Ctrl+C` to stop before continuing.

## 5. Configure the two PythonAnywhere tasks

Go to the **Tasks** tab.

**Scheduled Task** (daily job):
- Command: `/home/<you>/TradingAlpaca/.venv/bin/python /home/<you>/TradingAlpaca/scripts/run_daily.py`
- Time: PythonAnywhere schedules in **UTC**. US market close is 4:00pm ET,
  which is 20:00 UTC (EDT, summer) or 21:00 UTC (EST, winter). Schedule at
  **21:30 UTC** to be safely after close year-round without needing to
  adjust for daylight saving.

**Always-on Task** (Telegram bot):
- Command: `/home/<you>/TradingAlpaca/.venv/bin/python /home/<you>/TradingAlpaca/scripts/run_telegram_bot.py`

Using the venv's own `python` (full path) avoids needing to `source activate`
inside the task, since PythonAnywhere tasks don't run your shell profile.

## 6. Going from dry-run to live paper trading

Once you've watched a few days of dry-run output (check the Scheduled
Task's log in the Tasks tab, or `logs/*.jsonl` via the Files tab) and it
looks right:

```bash
nano .env   # set LIVE_TRADING_ENABLED=true
```

From the next scheduled run onward, the bot will actually submit orders —
to your **paper** account, per Section 13 of SPEC.md. Keep it on paper for
2-3+ months before even considering real capital.

## 7. Monitoring

- **Tasks tab** → each Scheduled Task run has its own stdout/stderr log.
- **Files tab** → `logs/signals_*.jsonl`, `logs/trades_*.jsonl`,
  `logs/screening_*.jsonl`, `logs/events_*.jsonl` — the full audit trail
  (SPEC.md Section 10).
- **Telegram** → daily summary after each scheduled run, plus `/status`
  on demand.

## Restart / outage safety

Alpaca's account state is the only source of truth the daily job trusts —
it never assumes it knows what it's holding from a previous run. A crashed
or skipped run just means one day's signals go unchecked, not a corrupted
or duplicated position (see the comment block at the top of
`halal_bot/live/daily_runner.py::run_once`).
