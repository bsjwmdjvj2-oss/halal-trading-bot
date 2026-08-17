"""Quarterly watchlist + compliance research agent (SPEC.md Section 6).

Advisory only, same principle as halal_bot.ai_summary: this never edits
data/watchlist.yaml or makes a trading decision — it researches and writes
a report for a human to review. Runs as a single Messages API call using
Anthropic's server-side web_search/web_fetch tools (Claude runs its own
search-and-read loop server-side; no client-side tool-execution code
needed here), not a Managed Agents session — this task is one-shot
research-and-report with no need for a persistent sandboxed workspace or
file editing, so the lighter-weight surface is the right fit.
"""
from __future__ import annotations

from halal_bot.config import CONFIG
from halal_bot.screening.watchlist import Instrument, load_watchlist

MODEL = "claude-opus-5"
MAX_TOOL_ROUNDS = 4  # each round can hit the server's own 10-search-iteration cap


def _build_prompt(instruments: list[Instrument]) -> str:
    cfg = CONFIG.screening
    by_sector: dict[str, list[str]] = {}
    for i in instruments:
        by_sector.setdefault(i.sector, []).append(i.ticker)
    watchlist_text = "\n".join(
        f"  {sector}: {', '.join(sorted(tickers))}" for sector, tickers in sorted(by_sector.items())
    )
    anchors = ", ".join(CONFIG.portfolio.anchor_etf_tickers)

    return f"""We run a halal-screened growth stock portfolio. The current candidate
watchlist ({len(instruments)} tickers, business-activity pre-screened) is:

{watchlist_text}

Every ticker here already passes an automated quantitative screen before it's
ever eligible for the portfolio:
  - Debt ratio (total debt / market cap) <= {cfg.max_debt_ratio:.0%}
  - Impure income ratio (interest + non-halal income / revenue) <= {cfg.max_impure_income_ratio:.0%}
  - Cash + interest-bearing securities / market cap <= {cfg.max_cash_securities_ratio:.0%}
  - Excluded sectors: {", ".join(cfg.excluded_sectors)}

We always hold two anchor ETFs regardless of signal: {anchors}.

You have two tasks. This is research for a human to review -- do not present
either as a final decision; the human decides whether to add anything to the
watchlist or flag a holding for the next compliance re-screen.

1. CANDIDATE DISCOVERY: propose 5-10 US-listed growth stocks NOT already in
   the watchlist above, spread across sectors we're currently light on.
   For each: 1-2 sentence business rationale, and flag anything from your
   research suggesting it might fail the quantitative ratios above (e.g.
   high leverage) or the excluded-sector list -- we'd rather know before
   spending API calls screening it.

2. COMPLIANCE RESEARCH: the quantitative ratio screen can't catch
   qualitative issues -- a business pivot, new subsidiary, M&A into a
   prohibited sector, or a recent controversy. Research recent (last
   ~6 months) news specifically for our two anchor ETFs ({anchors}) and
   for any watchlist tickers where something is publicly notable enough
   that it's worth a second look. Flag anything a purely financial-ratio
   screen would miss.

Write your findings as a clean, well-organized report in markdown. Use
clear section headers for the two tasks. Be concise per-ticker -- this is
a scan for things worth a human's attention, not an exhaustive due-diligence
report on each name."""


def run_quarterly_review() -> str:
    """Blocking; makes real API + web search calls. Returns the report text."""
    import os

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set — fill in .env before running research")

    from anthropic import Anthropic

    client = Anthropic()
    instruments = load_watchlist()
    prompt = _build_prompt(instruments)

    messages = [{"role": "user", "content": prompt}]
    tools = [
        {"type": "web_search_20260209", "name": "web_search"},
        {"type": "web_fetch_20260209", "name": "web_fetch"},
    ]

    response = client.messages.create(
        model=MODEL, max_tokens=8000, tools=tools, messages=messages
    )

    # The server runs its own search/fetch loop internally up to 10 rounds;
    # pause_turn means it hit that cap mid-research and needs a fresh request
    # to keep going, not a client-side tool result (these are server tools).
    rounds = 0
    while response.stop_reason == "pause_turn" and rounds < MAX_TOOL_ROUNDS:
        messages = [{"role": "user", "content": prompt}, {"role": "assistant", "content": response.content}]
        response = client.messages.create(
            model=MODEL, max_tokens=8000, tools=tools, messages=messages
        )
        rounds += 1

    report = "\n".join(block.text for block in response.content if block.type == "text")
    if not report.strip():
        raise RuntimeError(f"Empty report from research call (stop_reason={response.stop_reason})")
    return report
