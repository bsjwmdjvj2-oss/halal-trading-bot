"""TipRanks' official MCP-over-HTTP server (https://mcp.tipranks.com) --
real, unattended API-key access, confirmed against live calls (2026-08-22):
same 71 tools as the chat-session MCP connector this project used to depend
on, same response shapes. This replaces that connector for scheduled/
unattended refreshes -- see halal_bot.research.tipranks_context for why the
chat-only pattern existed and what it fed (news, Smart Score, analyst
consensus, AI Stock Analysis, all still stored via that module's
save_snapshot()).

Protocol: JSON-RPC 2.0 over a single POST endpoint (Streamable HTTP
transport) -- NOT plain REST like halal_bot.screening.halal_terminal_client.
Responses come back as either a bare JSON body or one SSE "message" event
wrapping a JSON payload (`event: message\\ndata: {...}`); _post_rpc handles
both. No session-ID handshake was required in testing (the server answered
tools/call directly after initialize with no Mcp-Session-Id header), so this
client does one initialize per TipRanksClient instance and reuses it.

Quota: 100 calls/month confirmed via get_usage() (a TipRanks
Premium/Ultimate subscriber benefit, above the base free tier's 50) --
resets calendar-monthly, shared across all API keys on the account. A full
watchlist refresh (consensus_ratings + ai_analysis_scores batched, plus the
three curated lists, market commentary, and a handful of headline news
tickers) costs roughly 15 calls, so refresh on a ~5-day cadence, not daily.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from halal_bot.config import CONFIG


class TipRanksNotConfiguredError(RuntimeError):
    pass


class TipRanksAPIError(RuntimeError):
    pass


@dataclass
class UsageStatus:
    tier: str
    used: int
    limit: int
    remaining: int
    resets_at: str


class TipRanksClient:
    def __init__(self):
        cfg = CONFIG.tipranks
        if not cfg.api_key:
            raise TipRanksNotConfiguredError(
                "TIPRANKS_API_KEY not set — fill in .env before using TipRanksClient"
            )
        import httpx

        self._url = f"{cfg.base_url}/mcp/?apikey={cfg.api_key}"
        self._headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        self._http = httpx.Client(timeout=30.0)
        self._next_id = 1
        self._initialized = False

    def _post_rpc(self, method: str, params: dict | None = None, _retries: int = 5) -> dict:
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._next_id += 1

        for attempt in range(_retries):
            resp = self._http.post(self._url, json=payload, headers=self._headers)
            if resp.status_code == 429:
                # Per-minute rate limit (confirmed live: free tier here is
                # 10/minute, separate from the 100/month quota) -- a full
                # refresh's ~14-16 calls fired back-to-back blows straight
                # through that, so this backs off and retries rather than
                # silently dropping data the way the caller previously did.
                import time
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else 15.0
                if attempt < _retries - 1:
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
            resp.raise_for_status()
            text = resp.text
            if text.startswith("event:"):
                for line in text.splitlines():
                    if line.startswith("data:"):
                        return json.loads(line[len("data:"):].strip())
                raise TipRanksAPIError(f"SSE response had no data line: {text[:200]!r}")
            return resp.json()
        raise TipRanksAPIError(f"{method}: exhausted retries on 429")

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        self._post_rpc("initialize", {
            "protocolVersion": "2026-06-18",
            "capabilities": {},
            "clientInfo": {"name": "halal-bot", "version": "1.0"},
        })
        self._initialized = True

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Low-level tools/call -- returns the parsed JSON payload from the
        tool's text content block. Raises TipRanksAPIError if the server
        reports isError or the content isn't the expected single text block
        (every tool observed so far returns exactly that shape)."""
        self._ensure_initialized()
        body = self._post_rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if "error" in body:
            raise TipRanksAPIError(f"{name}: {body['error']}")
        result = body.get("result", {})
        if result.get("isError"):
            raise TipRanksAPIError(f"{name}: {result}")
        content = result.get("content", [])
        if len(content) != 1 or content[0].get("type") != "text":
            raise TipRanksAPIError(f"{name}: unexpected content shape {content!r}")
        return json.loads(content[0]["text"])

    def get_usage(self) -> UsageStatus:
        data = self.call_tool("get_my_usage")
        return UsageStatus(
            tier=data.get("tier", ""),
            used=data.get("used", 0),
            limit=data.get("limit", 0),
            remaining=data.get("remaining", 0),
            resets_at=data.get("resets_at", ""),
        )

    def get_assets_data(self, tickers: list[str]) -> list[dict]:
        """Batched per-ticker consensus/price-target/Smart Score/etc --
        source for tipranks_context's consensus_ratings. One call per batch
        regardless of ticker count (tested up to 35)."""
        return self.call_tool("get_assets_data", {"tickers": tickers}).get("assetsData", [])

    def get_ai_stock_analysis(self, tickers: list[str]) -> dict:
        """Source for ai_analysis_scores. Caps at 25 tickers/call (server-
        enforced -- extras come back in dropped_tickers, batch accordingly)."""
        return self.call_tool("get_ai_stock_analysis", {"tickers": ",".join(tickers)})

    def get_top_smart_score_stocks(self) -> list[dict]:
        return self.call_tool("get_top_smart_score_stocks")

    def get_top_rated_stocks(self) -> list[dict]:
        return self.call_tool("get_top_rated_stocks")

    def get_market_commentary(self) -> dict:
        return self.call_tool("get_market_commentary")

    def get_assets_news(self, tickers: list[str], count: int = 20) -> list[dict]:
        return self.call_tool(
            "get_assets_news", {"tickers": tickers, "count": count}
        ).get("assetNewsArticles", [])
