"""Market data tool — stock/ETF quotes, history, news, and search via yfinance.

Read-only: this tool never places trades. See ``trading.py`` for order
execution against a brokerage account.
"""

from __future__ import annotations

import logging
from typing import Any, List

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)


@ToolRegistry.register("market_data")
class MarketDataTool(BaseTool):
    """Look up stock/ETF/crypto quotes, history, news, and ticker search."""

    tool_id = "market_data"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="market_data",
            description=(
                "Look up stock/ETF/crypto market data: real-time quotes,"
                " historical prices, recent company news, and ticker search."
                " Read-only — never places trades."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["quote", "history", "news", "search"],
                        "description": "Which lookup to perform.",
                    },
                    "symbols": {
                        "type": "string",
                        "description": (
                            "Comma-separated ticker symbols, e.g. 'AAPL,MSFT'."
                            " Required for quote/history/news."
                        ),
                    },
                    "period": {
                        "type": "string",
                        "description": (
                            "History range: '1d','5d','1mo','6mo','1y','5y',"
                            " etc. Default '1mo'."
                        ),
                    },
                    "interval": {
                        "type": "string",
                        "description": (
                            "History bar interval: '1d','1h','15m', etc."
                            " Default '1d'."
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "Free-text company/ticker search query."
                            " Required for action='search'."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": (
                            "Max news items or search results to return."
                            " Default 5."
                        ),
                    },
                },
                "required": ["action"],
            },
            category="finance",
            metadata={"requires_package": "yfinance"},
        )

    @staticmethod
    def _symbols(params: dict[str, Any]) -> List[str]:
        raw = str(params.get("symbols", ""))
        return [s.strip().upper() for s in raw.split(",") if s.strip()]

    def execute(self, **params: Any) -> ToolResult:
        action = params.get("action", "quote")

        try:
            import yfinance as yf
        except ImportError:
            return ToolResult(
                tool_name="market_data",
                content="yfinance not installed. Install with: pip install yfinance",
                success=False,
            )

        if action == "quote":
            return self._quote(yf, params)
        if action == "history":
            return self._history(yf, params)
        if action == "news":
            return self._news(yf, params)
        if action == "search":
            return self._search(yf, params)
        return ToolResult(
            tool_name="market_data",
            content=f"Unknown action: {action}. Use quote, history, news, or search.",
            success=False,
        )

    def _quote(self, yf: Any, params: dict[str, Any]) -> ToolResult:
        symbols = self._symbols(params)
        if not symbols:
            return ToolResult(
                tool_name="market_data",
                content="No symbols provided.",
                success=False,
            )

        lines: List[str] = []
        for sym in symbols:
            try:
                info = yf.Ticker(sym).info or {}
                price = info.get("regularMarketPrice") or info.get("currentPrice")
                prev_close = info.get("regularMarketPreviousClose") or info.get(
                    "previousClose"
                )
                currency = info.get("currency", "")
                name = info.get("shortName") or info.get("longName") or sym
                if price is None:
                    lines.append(f"{sym}: no price data available")
                    continue
                line = f"{sym} ({name}): {price} {currency}".strip()
                if prev_close:
                    change = price - prev_close
                    pct = (change / prev_close) * 100 if prev_close else 0.0
                    line += f" ({change:+.2f}, {pct:+.2f}%)"
                lines.append(line)
            except Exception as exc:
                logger.debug("market_data quote error for %s", sym, exc_info=True)
                lines.append(f"{sym}: error fetching quote ({exc})")

        return ToolResult(
            tool_name="market_data",
            content="\n".join(lines),
            success=True,
            metadata={"action": "quote", "symbols": symbols},
        )

    def _history(self, yf: Any, params: dict[str, Any]) -> ToolResult:
        symbols = self._symbols(params)
        if not symbols:
            return ToolResult(
                tool_name="market_data",
                content="No symbols provided.",
                success=False,
            )

        period = params.get("period", "1mo")
        interval = params.get("interval", "1d")

        sections: List[str] = []
        for sym in symbols:
            try:
                hist = yf.Ticker(sym).history(period=period, interval=interval)
                if hist is None or hist.empty:
                    sections.append(f"### {sym}\nNo historical data available.")
                    continue
                rows = []
                for idx, row in hist.tail(10).iterrows():
                    date = str(idx)[:10]
                    close = row.get("Close")
                    rows.append(f"{date}: close {close:.2f}")
                sections.append(f"### {sym} ({period}, {interval})\n" + "\n".join(rows))
            except Exception as exc:
                logger.debug("market_data history error for %s", sym, exc_info=True)
                sections.append(f"### {sym}\nError fetching history: {exc}")

        return ToolResult(
            tool_name="market_data",
            content="\n\n".join(sections),
            success=True,
            metadata={"action": "history", "symbols": symbols, "period": period},
        )

    def _news(self, yf: Any, params: dict[str, Any]) -> ToolResult:
        symbols = self._symbols(params)
        if not symbols:
            return ToolResult(
                tool_name="market_data",
                content="No symbols provided.",
                success=False,
            )

        limit = int(params.get("limit", 5))
        sections: List[str] = []
        for sym in symbols:
            try:
                items = yf.Ticker(sym).news or []
                if not items:
                    sections.append(f"### {sym}\nNo recent news found.")
                    continue
                entries = []
                for item in items[:limit]:
                    content = item.get("content", item)
                    title = content.get("title", "Untitled")
                    publisher = (content.get("provider") or {}).get("displayName", "")
                    url = (content.get("canonicalUrl") or {}).get(
                        "url"
                    ) or item.get("link", "")
                    entries.append(f"- {title} ({publisher})\n  {url}")
                sections.append(f"### {sym}\n" + "\n".join(entries))
            except Exception as exc:
                logger.debug("market_data news error for %s", sym, exc_info=True)
                sections.append(f"### {sym}\nError fetching news: {exc}")

        return ToolResult(
            tool_name="market_data",
            content="\n\n".join(sections),
            success=True,
            metadata={"action": "news", "symbols": symbols},
        )

    def _search(self, yf: Any, params: dict[str, Any]) -> ToolResult:
        query = str(params.get("query", "")).strip()
        if not query:
            return ToolResult(
                tool_name="market_data",
                content="No search query provided.",
                success=False,
            )

        limit = int(params.get("limit", 5))
        try:
            searcher = yf.Search(query)
            quotes = getattr(searcher, "quotes", []) or []
        except Exception as exc:
            return ToolResult(
                tool_name="market_data",
                content=f"Search error: {exc}",
                success=False,
            )

        if not quotes:
            return ToolResult(
                tool_name="market_data",
                content=f"No results found for '{query}'.",
                success=True,
                metadata={"action": "search", "query": query},
            )

        lines = []
        for q in quotes[:limit]:
            symbol = q.get("symbol", "")
            name = q.get("shortname") or q.get("longname") or ""
            exch = q.get("exchange", "")
            lines.append(f"{symbol}: {name} ({exch})")

        return ToolResult(
            tool_name="market_data",
            content="\n".join(lines),
            success=True,
            metadata={"action": "search", "query": query, "num_results": len(lines)},
        )


__all__ = ["MarketDataTool"]
