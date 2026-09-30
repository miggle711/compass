"""Thin wrapper around Alpaca's Trading API (paper account).

Verified against alpaca-py's TradingClient plus one raw REST call for
account activities, which the SDK does not wrap for TradingClient.
"""

from __future__ import annotations

import os
from datetime import datetime
from uuid import UUID

import requests
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.models import Order
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    ReplaceOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

PAPER_BASE_URL = "https://paper-api.alpaca.markets"


class AlpacaBroker:
    """Executes and inspects orders against an Alpaca paper trading account.

    Also exposes last-trade price lookups (Market Data API) since the
    execution agent needs it for context before placing orders — not
    account-specific, but kept here rather than a separate client to
    avoid a second module for one method.

    Paper vs. live is fixed at construction time, not exposed as a
    parameter to callers, so a model/agent can never redirect an order
    to a live account.
    """

    def __init__(self, key_id: str, secret_key: str) -> None:
        """Build a client locked to the paper account for these keys."""
        self._key_id = key_id
        self._secret_key = secret_key
        self._client = TradingClient(key_id, secret_key, paper=True)
        self._data_client = StockHistoricalDataClient(key_id, secret_key)

    @classmethod
    def from_env(cls) -> "AlpacaBroker":
        """Build a broker from ALPACA_PAPER_KEY_ID/ALPACA_PAPER_SECRET_KEY."""
        # get Alpaca paper account keys from environment variablesre
        key_id = os.environ["ALPACA_PAPER_KEY_ID"]
        secret_key = os.environ["ALPACA_PAPER_SECRET_KEY"]
        return cls(key_id, secret_key)

    def place_market_order(
        self,
        symbol: str,
        side: OrderSide,
        time_in_force: TimeInForce = TimeInForce.DAY,
        qty: float | None = None,
        notional: float | None = None,
    ) -> Order:
        """Place a market order, sized by share qty or by dollar notional.

        Notional orders only work for market orders and are day-only, 
        Alpaca doesn't allow replacing them afterward.
        """
        if (qty is None) == (notional is None):
            raise ValueError("Provide exactly one of qty or notional")
        request = MarketOrderRequest(
            symbol=symbol,
            side=side,
            time_in_force=time_in_force,
            qty=qty,
            notional=notional,
        )
        return self._client.submit_order(request)

    def place_limit_order(
        self,
        symbol: str,
        side: OrderSide,
        qty: float,
        limit_price: float,
        time_in_force: TimeInForce = TimeInForce.DAY,
    ) -> Order:
        """Place a limit order for a fixed share quantity."""
        request = LimitOrderRequest(
            symbol=symbol,
            side=side,
            qty=qty,
            limit_price=limit_price,
            time_in_force=time_in_force,
        )
        return self._client.submit_order(request)

    def place_bracket_order(
        self,
        symbol: str,
        side: OrderSide,
        qty: float,
        take_profit_price: float,
        stop_loss_price: float,
        time_in_force: TimeInForce = TimeInForce.DAY,
    ) -> Order:
        """Place an entry order with attached take-profit and stop-loss legs.

        The exit legs only activate once the entry fills, and filling
        one exit leg cancels the other.
        """
        request = MarketOrderRequest(
            symbol=symbol,
            side=side,
            qty=qty,
            time_in_force=time_in_force,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=take_profit_price),
            stop_loss=StopLossRequest(stop_price=stop_loss_price),
        )
        return self._client.submit_order(request)

    def get_order(self, order_id: UUID | str) -> Order:
        """Look up a single order's current lifecycle status."""
        return self._client.get_order_by_id(order_id)

    def cancel_order(self, order_id: UUID | str) -> None:
        """Cancel an order. Alpaca rejects this once the order is terminal
        (filled, canceled, or expired)."""
        self._client.cancel_order_by_id(order_id)

    def replace_order(
        self,
        order_id: UUID | str,
        qty: float | None = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
    ) -> Order:
        """Replace an open order's qty/price, only the fields you pass change.

        Alpaca returns a new order with a new ID — the original is marked
        `replaced`, not mutated in place. Not supported for OTO orders or
        notional orders.
        """
        request = ReplaceOrderRequest(
            qty=qty,
            limit_price=limit_price,
            stop_price=stop_price,
        )
        return self._client.replace_order_by_id(order_id, request)

    def get_orders(
        self,
        status: str | None = None,
        after: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
    ) -> list[Order]:
        """List orders, one entry per order regardless of fill status.

        For fill-level detail (price, quantity actually executed), use
        get_trade_activities instead — this only returns order records.
        """
        request = GetOrdersRequest(status=status, after=after, until=until, limit=limit)
        return self._client.get_orders(request)

    def get_last_price(self, symbol: str) -> float:
        """Last traded price for a symbol, from the Market Data API.

        Not the same as a live quote/bid-ask spread — this is the most
        recent executed trade price, which can lag slightly.
        """
        request = StockLatestTradeRequest(symbol_or_symbols=symbol)
        trades = self._data_client.get_stock_latest_trade(request)
        return trades[symbol].price

    def get_trade_activities(
        self,
        after: str | None = None,
        until: str | None = None,
        page_token: str | None = None,
        page_size: int = 20,
    ) -> list[dict]:
        """Fetch TradeActivity records — the audit trail for fills.

        Defaults to the 20 most recent fills. Without a cap, this grows
        unboundedly as the account trades over time and can produce a
        response too large for a small model's context window to even
        accept as tool output (hit this directly: 66 activities was
        already over an 8000-token free-tier budget). Not wrapped by
        alpaca-py's TradingClient, so this calls the Trading API REST
        endpoint directly.
        """
        params: dict[str, str] = {"activity_types": "FILL", "page_size": str(page_size)}
        if after:
            params["after"] = after
        if until:
            params["until"] = until
        if page_token:
            params["page_token"] = page_token

        response = requests.get(
            f"{PAPER_BASE_URL}/v2/account/activities",
            headers={
                "APCA-API-KEY-ID": self._key_id,
                "APCA-API-SECRET-KEY": self._secret_key,
            },
            params=params,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()


if __name__ == "__main__":
    # Scratch area for manually poking the paper account.
    # Run with: uv run python -m compass.broker
    from dotenv import load_dotenv

    load_dotenv()

    broker = AlpacaBroker.from_env()

    orders = broker.get_orders(limit=5)
    for o in orders:
        print(o.id, o.symbol, o.status, o.qty, o.notional)

    activities = broker.get_trade_activities()
    print("activities:", activities)
