"""Tests for the trading tool (Alpaca-backed, with safety guardrails)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

import openjarvis.tools.trading as trading_module
from openjarvis.core.registry import ToolRegistry
from openjarvis.tools.trading import TradingTool


def _install_mock_alpaca(monkeypatch, client_instance=None):
    """Install a fake ``alpaca`` package tree into ``sys.modules``."""
    alpaca_mod = MagicMock()
    trading_mod = MagicMock()
    client_mod = MagicMock()
    enums_mod = MagicMock()
    requests_mod = MagicMock()

    trading_client_cls = MagicMock()
    if client_instance is not None:
        trading_client_cls.return_value = client_instance
    client_mod.TradingClient = trading_client_cls

    enums_mod.OrderSide = MagicMock(BUY="buy", SELL="sell")
    enums_mod.TimeInForce = MagicMock(DAY="day")
    enums_mod.QueryOrderStatus = MagicMock(OPEN="open", ALL="all")

    requests_mod.MarketOrderRequest = MagicMock()
    requests_mod.LimitOrderRequest = MagicMock()
    requests_mod.GetOrdersRequest = MagicMock()

    monkeypatch.setitem(sys.modules, "alpaca", alpaca_mod)
    monkeypatch.setitem(sys.modules, "alpaca.trading", trading_mod)
    monkeypatch.setitem(sys.modules, "alpaca.trading.client", client_mod)
    monkeypatch.setitem(sys.modules, "alpaca.trading.enums", enums_mod)
    monkeypatch.setitem(sys.modules, "alpaca.trading.requests", requests_mod)
    return trading_client_cls


def _account(equity="10000", cash="5000", buying_power="20000", status="ACTIVE"):
    acct = MagicMock()
    acct.equity = equity
    acct.cash = cash
    acct.buying_power = buying_power
    acct.portfolio_value = equity
    acct.status = status
    return acct


class TestTradingSpec:
    def test_spec_name_and_category(self):
        tool = TradingTool(api_key="k", secret_key="s")
        assert tool.spec.name == "trading"
        assert tool.spec.category == "finance"

    def test_spec_metadata_defaults_to_paper(self):
        tool = TradingTool(api_key="k", secret_key="s")
        assert tool.spec.metadata["default_mode"] == "paper"

    def test_tool_id(self):
        assert TradingTool.tool_id == "trading"

    def test_registry_registration(self):
        ToolRegistry.register_value("trading", TradingTool)
        assert ToolRegistry.contains("trading")

    def test_defaults_to_paper_mode(self, monkeypatch):
        monkeypatch.delenv("ALPACA_PAPER", raising=False)
        tool = TradingTool(api_key="k", secret_key="s")
        assert tool._paper is True


class TestTradingNotInstalled:
    def test_execute_alpaca_not_installed(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "alpaca", raising=False)
        monkeypatch.delitem(sys.modules, "alpaca.trading", raising=False)
        monkeypatch.delitem(sys.modules, "alpaca.trading.client", raising=False)
        import builtins

        original_import = builtins.__import__

        def _mock_import(name, *args, **kwargs):
            if name.startswith("alpaca"):
                raise ImportError("No module named 'alpaca'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _mock_import)

        tool = TradingTool(api_key="k", secret_key="s")
        result = tool.execute(action="account")
        assert result.success is False
        assert "alpaca-py not installed" in result.content


class TestTradingCredentials:
    def test_no_credentials(self, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        _install_mock_alpaca(monkeypatch)

        tool = TradingTool(api_key="", secret_key="")
        result = tool.execute(action="account")
        assert result.success is False
        assert "credentials not configured" in result.content


class TestTradingAccountAndPositions:
    def test_account_paper_mode(self, monkeypatch):
        client = MagicMock()
        client.get_account.return_value = _account()
        _install_mock_alpaca(monkeypatch, client_instance=client)

        tool = TradingTool(api_key="k", secret_key="s", paper=True)
        result = tool.execute(action="account")
        assert result.success is True
        assert "Mode: PAPER" in result.content
        assert "10000" in result.content

    def test_account_live_mode(self, monkeypatch):
        client = MagicMock()
        client.get_account.return_value = _account()
        _install_mock_alpaca(monkeypatch, client_instance=client)

        tool = TradingTool(api_key="k", secret_key="s", paper=False)
        result = tool.execute(action="account")
        assert result.success is True
        assert "Mode: LIVE" in result.content

    def test_positions_empty(self, monkeypatch):
        client = MagicMock()
        client.get_all_positions.return_value = []
        _install_mock_alpaca(monkeypatch, client_instance=client)

        tool = TradingTool(api_key="k", secret_key="s")
        result = tool.execute(action="positions")
        assert result.success is True
        assert "No open positions" in result.content
        assert result.metadata["count"] == 0

    def test_positions_with_holdings(self, monkeypatch):
        position = MagicMock(
            symbol="AAPL",
            qty="10",
            avg_entry_price="150",
            current_price="160",
            unrealized_pl="100",
            unrealized_plpc="0.0666",
        )
        client = MagicMock()
        client.get_all_positions.return_value = [position]
        _install_mock_alpaca(monkeypatch, client_instance=client)

        tool = TradingTool(api_key="k", secret_key="s")
        result = tool.execute(action="positions")
        assert result.success is True
        assert "AAPL" in result.content
        assert result.metadata["count"] == 1


class TestTradingOrders:
    def test_orders_empty(self, monkeypatch):
        client = MagicMock()
        client.get_orders.return_value = []
        _install_mock_alpaca(monkeypatch, client_instance=client)

        tool = TradingTool(api_key="k", secret_key="s")
        result = tool.execute(action="orders")
        assert result.success is True
        assert "No orders" in result.content

    def test_orders_with_results(self, monkeypatch):
        order = MagicMock(
            id="order-1",
            side="buy",
            qty="5",
            notional=None,
            symbol="AAPL",
            order_type="market",
            status="filled",
        )
        client = MagicMock()
        client.get_orders.return_value = [order]
        _install_mock_alpaca(monkeypatch, client_instance=client)

        tool = TradingTool(api_key="k", secret_key="s")
        result = tool.execute(action="orders")
        assert result.success is True
        assert "order-1" in result.content
        assert "AAPL" in result.content


class TestTradingPlaceOrderValidation:
    def test_missing_symbol_or_side(self, monkeypatch):
        _install_mock_alpaca(monkeypatch)
        tool = TradingTool(api_key="k", secret_key="s")
        result = tool.execute(action="place_order", symbol="", side="buy", qty=1)
        assert result.success is False
        assert "Provide 'symbol'" in result.content

    def test_missing_qty_and_notional(self, monkeypatch):
        _install_mock_alpaca(monkeypatch)
        tool = TradingTool(api_key="k", secret_key="s")
        result = tool.execute(action="place_order", symbol="AAPL", side="buy")
        assert result.success is False
        assert "qty" in result.content

    def test_limit_order_requires_limit_price(self, monkeypatch):
        client = MagicMock()
        _install_mock_alpaca(monkeypatch, client_instance=client)
        tool = TradingTool(api_key="k", secret_key="s")
        result = tool.execute(
            action="place_order", symbol="AAPL", side="buy", qty=1, type="limit"
        )
        assert result.success is False
        assert "limit_price" in result.content

    def test_kill_switch_env_blocks_order(self, monkeypatch):
        monkeypatch.setenv("TRADING_KILL_SWITCH", "true")
        _install_mock_alpaca(monkeypatch)
        tool = TradingTool(api_key="k", secret_key="s")
        result = tool.execute(action="place_order", symbol="AAPL", side="buy", qty=1)
        assert result.success is False
        assert "kill switch" in result.content.lower()

    def test_kill_switch_file_blocks_order(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TRADING_KILL_SWITCH", raising=False)
        kill_file = tmp_path / "trading_disabled"
        kill_file.write_text("")
        monkeypatch.setattr(trading_module, "_KILL_SWITCH_FILE", kill_file)
        _install_mock_alpaca(monkeypatch)
        tool = TradingTool(api_key="k", secret_key="s")
        result = tool.execute(action="place_order", symbol="AAPL", side="buy", qty=1)
        assert result.success is False
        assert "kill switch" in result.content.lower()

    def test_symbol_not_in_allowlist(self, monkeypatch):
        _install_mock_alpaca(monkeypatch)
        tool = TradingTool(api_key="k", secret_key="s", allowed_symbols="AAPL,MSFT")
        result = tool.execute(action="place_order", symbol="TSLA", side="buy", qty=1)
        assert result.success is False
        assert "allowlist" in result.content


class TestTradingPlaceOrderPaper:
    def test_paper_market_order_success(self, monkeypatch, tmp_path):
        order = MagicMock(id="order-123", status="accepted")
        client = MagicMock()
        client.submit_order.return_value = order
        _install_mock_alpaca(monkeypatch, client_instance=client)

        tool = TradingTool(
            api_key="k",
            secret_key="s",
            paper=True,
            state_path=str(tmp_path / "state.json"),
        )
        result = tool.execute(action="place_order", symbol="aapl", side="buy", qty=2)
        assert result.success is True
        assert "PAPER" in result.content
        assert "order-123" in result.content
        assert result.metadata["paper"] is True
        client.submit_order.assert_called_once()

    def test_paper_order_skips_live_safety_checks(self, monkeypatch, tmp_path):
        """Paper orders are never blocked by live trading gates."""
        order = MagicMock(id="order-1", status="accepted")
        client = MagicMock()
        client.submit_order.return_value = order
        _install_mock_alpaca(monkeypatch, client_instance=client)

        monkeypatch.delenv("TRADING_LIVE_CONFIRMED", raising=False)
        tool = TradingTool(
            api_key="k",
            secret_key="s",
            paper=True,
            max_order_value=1,  # would be far too low for a live order
            state_path=str(tmp_path / "state.json"),
        )
        result = tool.execute(
            action="place_order", symbol="AAPL", side="buy", notional=10000
        )
        assert result.success is True


class TestTradingPlaceOrderLive:
    def test_live_order_blocked_without_confirmation(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TRADING_LIVE_CONFIRMED", raising=False)
        client = MagicMock()
        _install_mock_alpaca(monkeypatch, client_instance=client)

        tool = TradingTool(
            api_key="k",
            secret_key="s",
            paper=False,
            state_path=str(tmp_path / "state.json"),
        )
        result = tool.execute(action="place_order", symbol="AAPL", side="buy", qty=1)
        assert result.success is False
        assert "TRADING_LIVE_CONFIRMED" in result.content
        client.submit_order.assert_not_called()

    def test_live_order_blocked_by_max_order_value(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TRADING_LIVE_CONFIRMED", "true")
        client = MagicMock()
        client.get_account.return_value = _account(equity="10000")
        _install_mock_alpaca(monkeypatch, client_instance=client)

        tool = TradingTool(
            api_key="k",
            secret_key="s",
            paper=False,
            max_order_value=500,
            state_path=str(tmp_path / "state.json"),
        )
        result = tool.execute(
            action="place_order", symbol="AAPL", side="buy", notional=1000
        )
        assert result.success is False
        assert "exceeds" in result.content
        client.submit_order.assert_not_called()

    def test_live_order_within_limit_succeeds(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TRADING_LIVE_CONFIRMED", "true")
        order = MagicMock(id="order-live-1", status="accepted")
        client = MagicMock()
        client.get_account.return_value = _account(equity="10000")
        client.submit_order.return_value = order
        _install_mock_alpaca(monkeypatch, client_instance=client)

        tool = TradingTool(
            api_key="k",
            secret_key="s",
            paper=False,
            max_order_value=500,
            state_path=str(tmp_path / "state.json"),
        )
        result = tool.execute(
            action="place_order", symbol="AAPL", side="buy", notional=100
        )
        assert result.success is True
        assert "LIVE" in result.content
        assert "order-live-1" in result.content

    def test_live_buy_blocked_by_daily_loss_limit(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TRADING_LIVE_CONFIRMED", "true")
        state_path = tmp_path / "state.json"
        state_path.write_text(
            '{"baseline_date": "2024-01-01", "baseline_equity": 10000}'
        )

        client = MagicMock()
        # 5% drop from baseline (10000 -> 9500), limit defaults to 3%
        client.get_account.return_value = _account(equity="9500")
        _install_mock_alpaca(monkeypatch, client_instance=client)

        tool = TradingTool(
            api_key="k",
            secret_key="s",
            paper=False,
            max_order_value=10000,
            daily_loss_limit_pct=3,
            state_path=str(state_path),
        )
        # Freeze "today" to match the stored baseline date.
        monkeypatch.setattr(
            trading_module,
            "datetime",
            MagicMock(
                now=lambda tz=None: __import__("datetime").datetime(
                    2024, 1, 1, tzinfo=tz
                )
            ),
        )

        result = tool.execute(
            action="place_order", symbol="AAPL", side="buy", notional=100
        )
        assert result.success is False
        assert "Daily loss limit" in result.content
        client.submit_order.assert_not_called()

    def test_live_sell_allowed_during_daily_loss_breach(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TRADING_LIVE_CONFIRMED", "true")
        state_path = tmp_path / "state.json"
        state_path.write_text(
            '{"baseline_date": "2024-01-01", "baseline_equity": 10000}'
        )

        order = MagicMock(id="order-sell-1", status="accepted")
        client = MagicMock()
        client.get_account.return_value = _account(equity="9500")
        client.submit_order.return_value = order
        _install_mock_alpaca(monkeypatch, client_instance=client)

        tool = TradingTool(
            api_key="k",
            secret_key="s",
            paper=False,
            max_order_value=10000,
            daily_loss_limit_pct=3,
            state_path=str(state_path),
        )
        monkeypatch.setattr(
            trading_module,
            "datetime",
            MagicMock(
                now=lambda tz=None: __import__("datetime").datetime(
                    2024, 1, 1, tzinfo=tz
                )
            ),
        )

        result = tool.execute(
            action="place_order", symbol="AAPL", side="sell", notional=100
        )
        assert result.success is True


class TestTradingCancelAndClose:
    def test_cancel_order_missing_id(self, monkeypatch):
        _install_mock_alpaca(monkeypatch)
        tool = TradingTool(api_key="k", secret_key="s")
        result = tool.execute(action="cancel_order")
        assert result.success is False

    def test_cancel_order_success(self, monkeypatch):
        client = MagicMock()
        _install_mock_alpaca(monkeypatch, client_instance=client)
        tool = TradingTool(api_key="k", secret_key="s")
        result = tool.execute(action="cancel_order", order_id="order-1")
        assert result.success is True
        client.cancel_order_by_id.assert_called_once_with("order-1")

    def test_cancel_all_orders(self, monkeypatch):
        client = MagicMock()
        _install_mock_alpaca(monkeypatch, client_instance=client)
        tool = TradingTool(api_key="k", secret_key="s")
        result = tool.execute(action="cancel_all_orders")
        assert result.success is True
        client.cancel_orders.assert_called_once()

    def test_close_position_missing_symbol(self, monkeypatch):
        _install_mock_alpaca(monkeypatch)
        tool = TradingTool(api_key="k", secret_key="s")
        result = tool.execute(action="close_position", symbol="")
        assert result.success is False

    def test_close_position_success(self, monkeypatch):
        client = MagicMock()
        _install_mock_alpaca(monkeypatch, client_instance=client)
        tool = TradingTool(api_key="k", secret_key="s")
        result = tool.execute(action="close_position", symbol="aapl")
        assert result.success is True
        client.close_position.assert_called_once_with("AAPL")


class TestTradingUnknownAction:
    def test_unknown_action(self, monkeypatch):
        _install_mock_alpaca(monkeypatch)
        tool = TradingTool(api_key="k", secret_key="s")
        result = tool.execute(action="bogus")
        assert result.success is False
        assert "Unknown action" in result.content


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
