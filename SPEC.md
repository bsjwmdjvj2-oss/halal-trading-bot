# Halal Growth Trading Agent Bot — Build Spec

## 1. Objective
A Python trading bot that trades a halal-screened, growth-oriented stock/ETF
portfolio on Alpaca (paper account first), sends Telegram updates, and runs
24/7 on PythonAnywhere.

## 2. Broker / Environment
- **Broker:** Alpaca — paper trading account, $20,000 starting capital
- **Hosting:** PythonAnywhere, paid "Hacker" tier or above
  (free tier has no always-on tasks and restricts outbound internet — will
  block Alpaca/market-data/Telegram calls)
- **Language:** Python

## 3. Universe & Screening
- **Watchlist:** 30–50 halal-eligible stocks/ETFs, screened down to a
  **10–12 position portfolio**
- **Halal screening:** AAOIFI-style compliance (debt ratio, interest income,
  prohibited sector exclusion). Needs a defined data source — either a paid
  screening API (e.g. Zoya, Musaffa) or a maintained internal list.
- **Re-screening cadence:** run compliance re-checks periodically (e.g.
  monthly), not just at entry — a holding can fall out of compliance over time
- **Anchor allocation:** optionally reserve 1–2 of the 10–12 slots for a
  broad halal ETF (e.g. SPUS, ISDW) as a portfolio stability anchor

## 4. Signal Generation (Entry/Exit)
- **Approach:** rules-based technical signals (moving averages, RSI, volume)
  — not a full LLM-agent-debate framework, to keep this free to run
- LLM is used separately (see Section 8) only for reading portfolio state and
  writing plain-English summaries — not for making the trade decision itself
- Signal logic, thresholds, and exact indicators to be defined during
  implementation and validated via backtesting (Section 9)

## 5. Risk Controls
| Rule | Value |
|---|---|
| Max position size | ~12–15% of portfolio |
| Stop-loss | 15–20% below entry |
| Profit-taking | Scale out ~25% of position after 30%+ gain (let winners run rather than hard-capping) |
| Portfolio drawdown pause | If total portfolio is down 15–20% from peak, stop opening new positions and alert via Telegram (no auto-liquidation) |
| Sector concentration cap | ~30–35% max in any single sector |

## 6. Rebalancing & Review
- **Rebalance cadence:** monthly (position weights)
- **Signal-triggered exits:** can happen anytime, independent of the monthly
  schedule
- **Strategy review:** quarterly deeper review of overall approach and
  universe

## 7. Portfolio Growth Objective
- Strategy should be tilted toward growth (not pure income/dividend or
  passive index tracking)

## 8. AI Agent Layer
- A separate lightweight agent reads live Alpaca portfolio state (positions,
  cash, P&L) and generates plain-English Telegram summaries
  (e.g. "portfolio up 2.3% this week, XYZ hit its stop-loss")
- This agent does **not** make trading decisions — it narrates the state the
  rules engine already acted on
- Keep this LLM usage low-frequency (e.g. daily/weekly summary calls) to
  control API cost, rather than calling it on every signal check

## 9. Backtesting
- Backtest the rules-based strategy against 2–3 years of historical data
  **before** starting paper trading
- Use backtest results to validate/tune thresholds in Section 4 and 5

## 10. Logging & Reliability
- Full audit trail: log every signal, trade, and the reasoning/inputs behind
  it (not just Telegram messages) — for later review before going live
- Graceful recovery: on restart or Alpaca API outage, the bot should check
  current state and resume correctly rather than missing or duplicating
  trades

## 11. Dashboard
- Daily / weekly / monthly P&L view
- Suggest including: win rate, max drawdown, and a risk-adjusted return
  metric (e.g. Sharpe-like ratio) — not just raw P&L — so paper-trading
  results are judged on more than returns alone

## 12. Telegram Interface
- **Alerts:** trade entries/exits, portfolio summaries, drawdown-pause
  notifications
- **Commands:**
  - `/status` — current holdings + P&L
  - `/pause` — manual kill-switch, stop all trading
  - `/resume` — resume trading
- Requires paid PythonAnywhere plan for reliable outbound access to
  api.telegram.org and Alpaca's API together

## 13. Path to Live Trading
- Run on paper for a meaningful stretch — 2–3+ months, ideally covering some
  down days — before considering a move to a real account
- Evaluate against the dashboard metrics in Section 11, not raw P&L alone
- Paper trading does not include slippage or emotional pressure — treat it as
  a check on logic/bugs, not a guarantee of live performance
