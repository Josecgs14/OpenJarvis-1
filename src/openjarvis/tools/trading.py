"""Trading tool — brokerage account access and order execution via Alpaca.

Safety model
------------
- Defaults to a **paper** (simulated-money) account. Set ``ALPACA_PAPER=false``
  to point at a live account.
- Live orders additionally require ``TRADING_LIVE_CONFIRMED=true``. Without
  it, ``place_order`` refuses to submit live orders.
- Live orders are capped per-order by ``TRADING_MAX_ORDER_VALUE`` (USD,
  default 500).
- A daily loss circuit breaker (``TRADING_DAILY_LOSS_LIMIT_PCT``, default 3%)
  blocks new *buy* orders once account equity drops by that percentage from
  the day's starting equity. Sell/close actions remain available so the user
  can still reduce risk.
- ``TRADING_KILL_SWITCH=true`` (or the presence of
  ``~/.openjarvis/trading_disabled``) disables order placement entirely.
- ``TRADING_ALLOWED_SYMBOLS`` (comma-separated) optionally restricts which
  symbols can be traded.

None of these limits apply to read-only actions (``account``, ``positions``,
``orders``).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

_DEFAULT_STATE_PATH = Path.home() / ".openjarvis" / "trading_state.json"
_KILL_SWITCH_FILE = Path.home() / ".openjarvis" / "trading_disabled"

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


@ToolRegistry.register("trading")
class TradingTool(BaseTool):
    """Brokerage account access and order execution via Alpaca."""

    tool_id = "trading"
    is_local = False

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        paper: Optional[bool] = None,
        max_order_value: Optional[float] = None,
        daily_loss_limit_pct: Optional[float] = None,
        allowed_symbols: Optional[str] = None,
        state_path: Optional[str] = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("ALPACA_API_KEY", "")
        self._secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY", "")
        self._paper = _env_bool("ALPACA_PAPER", True) if paper is None else paper
        self._max_order_value = (
            float(os.environ.get("TRADING_MAX_ORDER_VALUE", "500"))
            if max_order_value is None
            else max_order_value
        )
        self._daily_loss_limit_pct = (
            float(os.environ.get("TRADING_DAILY_LOSS_LIMIT_PCT", "3"))
            if daily_loss_limit_pct is None
            else daily_loss_limit_pct
        )
        raw_allowed = (
            os.environ.get("TRADING_ALLOWED_SYMBOLS", "")
            if allowed_symbols is None
            else allowed_symbols
        )
        self._allowed_symbols = {
            s.strip().upper() for s in raw_allowed.split(",") if s.strip()
        }
        self._state_path = Path(state_path) if state_path else _DEFAULT_STATE_PATH

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="trading",
            description=(
                "View a brokerage account (Alpaca) and place or cancel stock"
                " orders. Defaults to a PAPER (simulated-money) account."
                " Live trading additionally requires ALPACA_PAPER=false and"
                " TRADING_LIVE_CONFIRMED=true, and is subject to a per-order"
                " value cap (TRADING_MAX_ORDER_VALUE) and a daily loss"
                " circuit breaker (TRADING_DAILY_LOSS_LIMIT_PCT)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "account",
                            "positions",
                            "orders",
                            "place_order",
                            "cancel_order",
                            "cancel_all_orders",
                            "close_position",
                        ],
                        "description": "Operation to perform.",
                    },
                    "symbol": {
                        "type": "string",
                        "description": (
                            "Ticker symbol, e.g. 'AAPL'. Required for"
                            " place_order/close_position."
                        ),
                    },
                    "side": {
                        "type": "string",
                        "enum": ["buy", "sell"],
                        "description": "Order side. Required for place_order.",
                    },
                    "qty": {
                        "type": "number",
                        "description": (
                            "Number of shares (fractional allowed). Use this"
                            " or 'notional'."
                        ),
                    },
                    "notional": {
                        "type": "number",
                        "description": (
                            "Dollar amount to buy/sell. Use this or 'qty'."
                        ),
                    },
                    "type": {
                        "type": "string",
                        "enum": ["market", "limit"],
                        "description": "Order type. Default 'market'.",
                    },
                    "limit_price": {
                        "type": "number",
                        "description": "Limit price. Required when type='limit'.",
                    },
                    "order_id": {
                        "type": "string",
                        "description": "Order ID. Required for cancel_order.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["open", "all"],
                        "description": "Filter for 'orders' action. Default 'open'.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": (
                            "Max orders to return for 'orders' action. Default 20."
                        ),
                    },
                },
                "required": ["action"],
            },
            category="finance",
            metadata={
                "requires_package": "alpaca-py",
                "requires_api_key": "ALPACA_API_KEY,ALPACA_SECRET_KEY",
                "default_mode": "paper",
            },
        )

    # -- client construction -----------------------------------------------

    def _client(self) -> Any:
        from alpaca.trading.client import TradingClient

        if not self._api_key or not self._secret_key:
            raise RuntimeError(
                "Alpaca API credentials not configured."
                " Set ALPACA_API_KEY and ALPACA_SECRET_KEY."
            )
        return TradingClient(self._api_key, self._secret_key, paper=self._paper)

    # -- safety helpers -------------------------------------------------------

    def _kill_switch_active(self) -> bool:
        if _env_bool("TRADING_KILL_SWITCH", False):
            return True
        return _KILL_SWITCH_FILE.exists()

    def _live_trading_confirmed(self) -> bool:
        return _env_bool("TRADING_LIVE_CONFIRMED", False)

    def _load_state(self) -> dict[str, Any]:
        try:
            return json.loads(self._state_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save_state(self, state: dict[str, Any]) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(state))
        except OSError:
            logger.debug("Failed to persist trading state", exc_info=True)

    def _daily_loss_breach(self, client: Any) -> Optional[str]:
        """Return a description of the breach if today's loss limit is hit."""
        try:
            equity = float(client.get_account().equity)
        except Exception:
            return None

        today = datetime.now(timezone.utc).date().isoformat()
        state = self._load_state()
        if state.get("baseline_date") != today or state.get("baseline_equity") is None:
            state["baseline_date"] = today
            state["baseline_equity"] = equity
            self._save_state(state)
            return None

        baseline = float(state["baseline_equity"])
        if baseline <= 0:
            return None
        drop_pct = (baseline - equity) / baseline * 100
        if drop_pct >= self._daily_loss_limit_pct:
            return f"-{drop_pct:.2f}% today (limit {self._daily_loss_limit_pct:.2f}%)"
        return None

    def _estimate_order_value(
        self,
        symbol: str,
        qty: Any,
        notional: Any,
        limit_price: Any,
    ) -> Optional[float]:
        if notional is not None:
            try:
                return float(notional)
            except (TypeError, ValueError):
                return None
        if qty is None:
            return None
        price = limit_price
        if price is None:
            try:
                import yfinance as yf

                info = yf.Ticker(symbol).info or {}
                price = info.get("regularMarketPrice") or info.get("currentPrice")
            except Exception:
                price = None
        if price is None:
            return None
        try:
            return float(qty) * float(price)
        except (TypeError, ValueError):
            return None

    # -- execute -----------------------------------------------------------

    def execute(self, **params: Any) -> ToolResult:
        action = params.get("action", "account")

        try:
            from alpaca.trading.client import TradingClient  # noqa: F401
        except ImportError:
            return ToolResult(
                tool_name="trading",
                content="alpaca-py not installed. Install with: pip install alpaca-py",
                success=False,
            )

        try:
            if action == "account":
                return self._account()
            if action == "positions":
                return self._positions()
            if action == "orders":
                return self._orders(params)
            if action == "place_order":
                return self._place_order(params)
            if action == "cancel_order":
                return self._cancel_order(params)
            if action == "cancel_all_orders":
                return self._cancel_all_orders()
            if action == "close_position":
                return self._close_position(params)
            return ToolResult(
                tool_name="trading",
                content=f"Unknown action: {action}",
                success=False,
            )
        except RuntimeError as exc:
            return ToolResult(tool_name="trading", content=str(exc), success=False)
        except Exception as exc:
            logger.debug("trading tool error", exc_info=True)
            return ToolResult(
                tool_name="trading",
                content=f"Trading error: {exc}",
                success=False,
            )

    # -- read-only actions ---------------------------------------------------

    def _account(self) -> ToolResult:
        client = self._client()
        acct = client.get_account()
        mode = "PAPER" if self._paper else "LIVE"
        lines = [
            f"Mode: {mode}",
            f"Status: {acct.status}",
            f"Equity: {acct.equity}",
            f"Cash: {acct.cash}",
            f"Buying power: {acct.buying_power}",
            f"Portfolio value: {acct.portfolio_value}",
        ]
        return ToolResult(
            tool_name="trading",
            content="\n".join(lines),
            success=True,
            metadata={"action": "account", "paper": self._paper},
        )

    def _positions(self) -> ToolResult:
        client = self._client()
        positions = client.get_all_positions()
        if not positions:
            return ToolResult(
                tool_name="trading",
                content="No open positions.",
                success=True,
                metadata={"action": "positions", "count": 0},
            )
        lines = [
            f"{p.symbol}: {p.qty} shares @ avg ${p.avg_entry_price}, "
            f"current ${p.current_price}, "
            f"unrealized P/L ${p.unrealized_pl} ({p.unrealized_plpc})"
            for p in positions
        ]
        return ToolResult(
            tool_name="trading",
            content="\n".join(lines),
            success=True,
            metadata={"action": "positions", "count": len(positions)},
        )

    def _orders(self, params: dict[str, Any]) -> ToolResult:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        client = self._client()
        status = params.get("status", "open")
        req = GetOrdersRequest(
            status=QueryOrderStatus.OPEN if status == "open" else QueryOrderStatus.ALL,
            limit=int(params.get("limit", 20)),
        )
        orders = client.get_orders(filter=req)
        if not orders:
            return ToolResult(
                tool_name="trading",
                content="No orders.",
                success=True,
                metadata={"action": "orders", "count": 0},
            )
        lines = [
            f"{o.id}: {o.side} {o.qty or o.notional} {o.symbol}"
            f" ({o.order_type}, {o.status})"
            for o in orders
        ]
        return ToolResult(
            tool_name="trading",
            content="\n".join(lines),
            success=True,
            metadata={"action": "orders", "count": len(orders)},
        )

    # -- order placement -------------------------------------------------------

    def _place_order(self, params: dict[str, Any]) -> ToolResult:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

        if self._kill_switch_active():
            return ToolResult(
                tool_name="trading",
                content=(
                    "Trading is disabled (kill switch active)."
                    " Unset TRADING_KILL_SWITCH or remove"
                    f" {_KILL_SWITCH_FILE} to re-enable."
                ),
                success=False,
            )

        symbol = str(params.get("symbol", "")).strip().upper()
        side_raw = str(params.get("side", "")).strip().lower()
        if not symbol or side_raw not in ("buy", "sell"):
            return ToolResult(
                tool_name="trading",
                content="Provide 'symbol' and 'side' ('buy' or 'sell').",
                success=False,
            )

        qty = params.get("qty")
        notional = params.get("notional")
        if qty is None and notional is None:
            return ToolResult(
                tool_name="trading",
                content="Provide either 'qty' (shares) or 'notional' (dollar amount).",
                success=False,
            )

        order_type = str(params.get("type", "market")).strip().lower()
        limit_price = params.get("limit_price")

        if self._allowed_symbols and symbol not in self._allowed_symbols:
            return ToolResult(
                tool_name="trading",
                content=(
                    f"Symbol {symbol} is not in the configured allowlist"
                    " (TRADING_ALLOWED_SYMBOLS)."
                ),
                success=False,
            )

        client = self._client()

        if not self._paper:
            if not self._live_trading_confirmed():
                return ToolResult(
                    tool_name="trading",
                    content=(
                        "Live trading is not enabled. This tool is configured"
                        " for a LIVE (real-money) account, but"
                        " TRADING_LIVE_CONFIRMED is not set. Set"
                        " ALPACA_PAPER=true to use a paper account, or set"
                        " TRADING_LIVE_CONFIRMED=true to allow live orders"
                        " (at your own risk)."
                    ),
                    success=False,
                )

            order_value = self._estimate_order_value(symbol, qty, notional, limit_price)
            if order_value is not None and order_value > self._max_order_value:
                return ToolResult(
                    tool_name="trading",
                    content=(
                        f"Order value (~${order_value:.2f}) exceeds the"
                        f" configured live trading limit of"
                        f" ${self._max_order_value:.2f}"
                        " (TRADING_MAX_ORDER_VALUE). Reduce the order size or"
                        " raise the limit."
                    ),
                    success=False,
                )

            if side_raw == "buy":
                breach = self._daily_loss_breach(client)
                if breach is not None:
                    return ToolResult(
                        tool_name="trading",
                        content=(
                            f"Daily loss limit reached ({breach}). New buy"
                            " orders are blocked for the rest of the day."
                            " Sell orders to reduce risk are still allowed."
                        ),
                        success=False,
                    )

        side = OrderSide.BUY if side_raw == "buy" else OrderSide.SELL
        order_kwargs: dict[str, Any] = (
            {"qty": float(qty)} if qty is not None else {"notional": float(notional)}
        )

        if order_type == "limit":
            if limit_price is None:
                return ToolResult(
                    tool_name="trading",
                    content="'limit_price' is required for limit orders.",
                    success=False,
                )
            request = LimitOrderRequest(
                symbol=symbol,
                side=side,
                time_in_force=TimeInForce.DAY,
                limit_price=float(limit_price),
                **order_kwargs,
            )
        else:
            request = MarketOrderRequest(
                symbol=symbol,
                side=side,
                time_in_force=TimeInForce.DAY,
                **order_kwargs,
            )

        order = client.submit_order(order_data=request)
        mode = "PAPER" if self._paper else "LIVE"
        size_desc = f"{qty} shares" if qty is not None else f"${notional}"
        return ToolResult(
            tool_name="trading",
            content=(
                f"Order submitted ({mode}): {side_raw} {size_desc} {symbol}"
                f" ({order_type}). Order ID: {order.id}, status: {order.status}"
            ),
            success=True,
            metadata={
                "action": "place_order",
                "order_id": str(order.id),
                "paper": self._paper,
            },
        )

    def _cancel_order(self, params: dict[str, Any]) -> ToolResult:
        order_id = str(params.get("order_id", "")).strip()
        if not order_id:
            return ToolResult(
                tool_name="trading",
                content="Provide 'order_id'.",
                success=False,
            )
        client = self._client()
        client.cancel_order_by_id(order_id)
        return ToolResult(
            tool_name="trading",
            content=f"Cancelled order {order_id}.",
            success=True,
            metadata={"action": "cancel_order", "order_id": order_id},
        )

    def _cancel_all_orders(self) -> ToolResult:
        client = self._client()
        client.cancel_orders()
        return ToolResult(
            tool_name="trading",
            content="All open orders cancelled.",
            success=True,
            metadata={"action": "cancel_all_orders"},
        )

    def _close_position(self, params: dict[str, Any]) -> ToolResult:
        symbol = str(params.get("symbol", "")).strip().upper()
        if not symbol:
            return ToolResult(
                tool_name="trading",
                content="Provide 'symbol'.",
                success=False,
            )
        client = self._client()
        client.close_position(symbol)
        return ToolResult(
            tool_name="trading",
            content=f"Closing position {symbol}.",
            success=True,
            metadata={"action": "close_position", "symbol": symbol},
        )


__all__ = ["TradingTool"]
