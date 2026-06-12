"""Tests for the market data tool."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from openjarvis.core.registry import ToolRegistry
from openjarvis.tools.market_data import MarketDataTool


class TestMarketDataSpec:
    def test_spec_name_and_category(self):
        tool = MarketDataTool()
        assert tool.spec.name == "market_data"
        assert tool.spec.category == "finance"

    def test_spec_requires_package_metadata(self):
        tool = MarketDataTool()
        assert tool.spec.metadata["requires_package"] == "yfinance"

    def test_spec_action_required(self):
        tool = MarketDataTool()
        assert "action" in tool.spec.parameters["required"]

    def test_tool_id(self):
        assert MarketDataTool.tool_id == "market_data"

    def test_registry_registration(self):
        ToolRegistry.register_value("market_data", MarketDataTool)
        assert ToolRegistry.contains("market_data")


class TestMarketDataNoYfinance:
    def test_execute_yfinance_not_installed(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "yfinance", raising=False)
        import builtins

        original_import = builtins.__import__

        def _mock_import(name, *args, **kwargs):
            if name == "yfinance":
                raise ImportError("No module named 'yfinance'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _mock_import)

        tool = MarketDataTool()
        result = tool.execute(action="quote", symbols="AAPL")
        assert result.success is False
        assert "yfinance not installed" in result.content


class TestMarketDataQuote:
    def test_quote_no_symbols(self, monkeypatch):
        mock_yf = MagicMock()
        monkeypatch.setitem(sys.modules, "yfinance", mock_yf)

        tool = MarketDataTool()
        result = tool.execute(action="quote", symbols="")
        assert result.success is False
        assert "No symbols" in result.content

    def test_quote_single_symbol(self, monkeypatch):
        mock_ticker = MagicMock()
        mock_ticker.info = {
            "regularMarketPrice": 150.0,
            "regularMarketPreviousClose": 145.0,
            "currency": "USD",
            "shortName": "Apple Inc.",
        }
        mock_yf = MagicMock()
        mock_yf.Ticker.return_value = mock_ticker
        monkeypatch.setitem(sys.modules, "yfinance", mock_yf)

        tool = MarketDataTool()
        result = tool.execute(action="quote", symbols="aapl")
        assert result.success is True
        assert "AAPL" in result.content
        assert "150.0" in result.content
        assert "+5.00" in result.content
        assert result.metadata["symbols"] == ["AAPL"]

    def test_quote_multiple_symbols(self, monkeypatch):
        mock_ticker = MagicMock()
        mock_ticker.info = {"regularMarketPrice": 100.0, "currency": "USD"}
        mock_yf = MagicMock()
        mock_yf.Ticker.return_value = mock_ticker
        monkeypatch.setitem(sys.modules, "yfinance", mock_yf)

        tool = MarketDataTool()
        result = tool.execute(action="quote", symbols="AAPL, MSFT")
        assert result.success is True
        assert result.metadata["symbols"] == ["AAPL", "MSFT"]
        assert result.content.count("100.0") == 2

    def test_quote_handles_missing_price(self, monkeypatch):
        mock_ticker = MagicMock()
        mock_ticker.info = {}
        mock_yf = MagicMock()
        mock_yf.Ticker.return_value = mock_ticker
        monkeypatch.setitem(sys.modules, "yfinance", mock_yf)

        tool = MarketDataTool()
        result = tool.execute(action="quote", symbols="XXXX")
        assert result.success is True
        assert "no price data available" in result.content

    def test_quote_handles_ticker_error(self, monkeypatch):
        mock_yf = MagicMock()
        mock_yf.Ticker.side_effect = RuntimeError("boom")
        monkeypatch.setitem(sys.modules, "yfinance", mock_yf)

        tool = MarketDataTool()
        result = tool.execute(action="quote", symbols="AAPL")
        assert result.success is True
        assert "error fetching quote" in result.content


class TestMarketDataHistory:
    def test_history_no_symbols(self, monkeypatch):
        mock_yf = MagicMock()
        monkeypatch.setitem(sys.modules, "yfinance", mock_yf)

        tool = MarketDataTool()
        result = tool.execute(action="history", symbols="")
        assert result.success is False

    def test_history_empty(self, monkeypatch):
        mock_hist = MagicMock()
        mock_hist.empty = True
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = mock_hist
        mock_yf = MagicMock()
        mock_yf.Ticker.return_value = mock_ticker
        monkeypatch.setitem(sys.modules, "yfinance", mock_yf)

        tool = MarketDataTool()
        result = tool.execute(action="history", symbols="AAPL")
        assert result.success is True
        assert "No historical data available" in result.content

    def test_history_with_rows(self, monkeypatch):
        mock_hist = MagicMock()
        mock_hist.empty = False
        row = {"Close": 123.45}
        mock_hist.tail.return_value.iterrows.return_value = [("2024-01-02", row)]
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = mock_hist
        mock_yf = MagicMock()
        mock_yf.Ticker.return_value = mock_ticker
        monkeypatch.setitem(sys.modules, "yfinance", mock_yf)

        tool = MarketDataTool()
        result = tool.execute(action="history", symbols="AAPL", period="1mo", interval="1d")
        assert result.success is True
        assert "AAPL" in result.content
        assert "123.45" in result.content
        assert "2024-01-02" in result.content


class TestMarketDataNews:
    def test_news_no_symbols(self, monkeypatch):
        mock_yf = MagicMock()
        monkeypatch.setitem(sys.modules, "yfinance", mock_yf)

        tool = MarketDataTool()
        result = tool.execute(action="news", symbols="")
        assert result.success is False

    def test_news_no_items(self, monkeypatch):
        mock_ticker = MagicMock()
        mock_ticker.news = []
        mock_yf = MagicMock()
        mock_yf.Ticker.return_value = mock_ticker
        monkeypatch.setitem(sys.modules, "yfinance", mock_yf)

        tool = MarketDataTool()
        result = tool.execute(action="news", symbols="AAPL")
        assert result.success is True
        assert "No recent news found" in result.content

    def test_news_with_items(self, monkeypatch):
        mock_ticker = MagicMock()
        mock_ticker.news = [
            {
                "content": {
                    "title": "Apple announces new product",
                    "provider": {"displayName": "Reuters"},
                    "canonicalUrl": {"url": "https://example.com/news/1"},
                }
            }
        ]
        mock_yf = MagicMock()
        mock_yf.Ticker.return_value = mock_ticker
        monkeypatch.setitem(sys.modules, "yfinance", mock_yf)

        tool = MarketDataTool()
        result = tool.execute(action="news", symbols="AAPL", limit=3)
        assert result.success is True
        assert "Apple announces new product" in result.content
        assert "Reuters" in result.content
        assert "https://example.com/news/1" in result.content


class TestMarketDataSearch:
    def test_search_no_query(self, monkeypatch):
        mock_yf = MagicMock()
        monkeypatch.setitem(sys.modules, "yfinance", mock_yf)

        tool = MarketDataTool()
        result = tool.execute(action="search", query="")
        assert result.success is False

    def test_search_no_results(self, monkeypatch):
        mock_searcher = MagicMock()
        mock_searcher.quotes = []
        mock_yf = MagicMock()
        mock_yf.Search.return_value = mock_searcher
        monkeypatch.setitem(sys.modules, "yfinance", mock_yf)

        tool = MarketDataTool()
        result = tool.execute(action="search", query="nonexistent")
        assert result.success is True
        assert "No results found" in result.content

    def test_search_with_results(self, monkeypatch):
        mock_searcher = MagicMock()
        mock_searcher.quotes = [
            {"symbol": "AAPL", "shortname": "Apple Inc.", "exchange": "NMS"},
        ]
        mock_yf = MagicMock()
        mock_yf.Search.return_value = mock_searcher
        monkeypatch.setitem(sys.modules, "yfinance", mock_yf)

        tool = MarketDataTool()
        result = tool.execute(action="search", query="apple")
        assert result.success is True
        assert "AAPL" in result.content
        assert "Apple Inc." in result.content


class TestMarketDataUnknownAction:
    def test_unknown_action(self, monkeypatch):
        mock_yf = MagicMock()
        monkeypatch.setitem(sys.modules, "yfinance", mock_yf)

        tool = MarketDataTool()
        result = tool.execute(action="bogus")
        assert result.success is False
        assert "Unknown action" in result.content
