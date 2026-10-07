"""Trade execution subagent: places and manages orders on the user's
Alpaca paper account.

Tools here are thin translators — typed args in, AlpacaBroker call,
typed result out. All Alpaca-specific logic lives in broker.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from alpaca.trading.enums import OrderSide
from dotenv import load_dotenv
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from compass.broker import AlpacaBroker
from compass.models import OrderResult

load_dotenv()

# Order size guardrails (SCRUM-17). Enforced here, not in AlpacaBroker,
# since these are Compass policy, not an Alpaca API constraint.
MAX_ORDER_QTY = 100
MAX_ORDER_NOTIONAL = 10_000.0


@dataclass
class ExecutionDeps:
    broker: AlpacaBroker


class OrderTooLargeError(Exception):
    """Raised when a requested order exceeds MAX_ORDER_QTY or
    MAX_ORDER_NOTIONAL. Caught in each placing tool and turned into a
    ModelRetry so the agent explains the limit instead of the run
    crashing."""


_model = OpenAIChatModel(
    "openai/gpt-oss-120b",
    provider=OpenAIProvider(
        base_url="https://api.groq.com/openai/v1",
        api_key=os.environ["GROQ_API_KEY"],
    ),
)

SYSTEM_PROMPT = """\
You execute trades on the user's Alpaca paper trading account. You only \
act on explicit instructions, you never recommend what to buy, sell, or \
hold. When you take an action, state clearly that it affected the paper \
account, not a live one.

For get_order_status, cancel_order, and replace_order: only call these if \
the user's message already contains the order ID. If they refer to an \
order without giving its ID (e.g. "my last AAPL order", "my pending TSLA \
order"), do NOT guess which order they mean and do NOT call \
get_order_status/cancel_order/replace_order to try to resolve it \
yourself. Instead, ask them to provide the order ID, or offer to call \
get_trade_history and show them the list so they can pick the right one. \
Acting on the wrong order is worse than asking.

get_trade_history itself has no such restriction, call it freely \
whenever the user asks about past trades/fills (e.g. "did my MSFT order \
fill, and at what price", "what have I traded this week"). It only \
returns a list, there's no risk of acting on the wrong order from it.\
"""

execution_agent = Agent(
    _model,
    deps_type=ExecutionDeps,
    output_type=OrderResult | str,
    system_prompt=SYSTEM_PROMPT,
)


def _to_result(order, account_mode: str = "paper") -> OrderResult:
    return OrderResult(
        order_id=str(order.id),
        status=order.status.value,
        symbol=order.symbol,
        qty=order.qty,
        notional=order.notional,
        account_mode=account_mode,
    )


def _check_order_size(
    broker: AlpacaBroker, symbol: str, qty: float | None, notional: float | None
) -> None:
    """Reject orders over MAX_ORDER_QTY shares or MAX_ORDER_NOTIONAL
    dollars. For qty orders, looks up the last price to compute an
    equivalent notional, so a small share count in an expensive stock
    can't bypass the dollar limit."""
    if notional is not None and notional > MAX_ORDER_NOTIONAL:
        raise OrderTooLargeError(
            f"${notional:,.2f} exceeds the ${MAX_ORDER_NOTIONAL:,.2f} order limit."
        )
    if qty is not None:
        if qty > MAX_ORDER_QTY:
            raise OrderTooLargeError(
                f"{qty} shares exceeds the {MAX_ORDER_QTY}-share order limit."
            )
        implied_notional = qty * broker.get_last_price(symbol)
        if implied_notional > MAX_ORDER_NOTIONAL:
            raise OrderTooLargeError(
                f"{qty} shares of {symbol} is ~${implied_notional:,.2f}, "
                f"which exceeds the ${MAX_ORDER_NOTIONAL:,.2f} order limit."
            )


@execution_agent.tool
def place_market_order(
    ctx: RunContext[ExecutionDeps],
    symbol: str,
    side: str,
    qty: float | None = None,
    notional: float | None = None,
) -> OrderResult:
    """Buy or sell at the current market price. Use qty for a share count,
    or notional for a dollar amount (market orders only)."""
    try:
        _check_order_size(ctx.deps.broker, symbol, qty, notional)
    except OrderTooLargeError as e:
        raise ModelRetry(str(e))
    order = ctx.deps.broker.place_market_order(
        symbol, OrderSide(side), qty=qty, notional=notional
    )
    return _to_result(order)


@execution_agent.tool
def place_limit_order(
    ctx: RunContext[ExecutionDeps],
    symbol: str,
    side: str,
    qty: float,
    limit_price: float,
) -> OrderResult:
    """Buy or sell a fixed share quantity, only at limit_price or better."""
    try:
        _check_order_size(ctx.deps.broker, symbol, qty, None)
    except OrderTooLargeError as e:
        raise ModelRetry(str(e))
    order = ctx.deps.broker.place_limit_order(symbol, OrderSide(side), qty, limit_price)
    return _to_result(order)


@execution_agent.tool
def place_bracket_order(
    ctx: RunContext[ExecutionDeps],
    symbol: str,
    side: str,
    qty: float,
    take_profit_price: float,
    stop_loss_price: float,
) -> OrderResult:
    """Market entry with an attached take-profit and stop-loss. Whichever
    exit condition triggers first fills and cancels the other."""
    try:
        _check_order_size(ctx.deps.broker, symbol, qty, None)
    except OrderTooLargeError as e:
        raise ModelRetry(str(e))
    order = ctx.deps.broker.place_bracket_order(
        symbol, OrderSide(side), qty, take_profit_price, stop_loss_price
    )
    return _to_result(order)


@execution_agent.tool
def get_order_status(ctx: RunContext[ExecutionDeps], order_id: str) -> OrderResult:
    """Look up an order's current lifecycle status."""
    return _to_result(ctx.deps.broker.get_order(order_id))


@execution_agent.tool
def get_last_price(ctx: RunContext[ExecutionDeps], symbol: str) -> float:
    """Get a symbol's last traded price. Use this for context before
    placing limit or bracket orders — not a live quote, may lag slightly."""
    return ctx.deps.broker.get_last_price(symbol)


@execution_agent.tool
def cancel_order(ctx: RunContext[ExecutionDeps], order_id: str) -> str:
    """Cancel an order. Only works before the order reaches a terminal
    state (filled, canceled, or expired)."""
    ctx.deps.broker.cancel_order(order_id)
    return f"Order {order_id} canceled."


@execution_agent.tool
def replace_order(
    ctx: RunContext[ExecutionDeps],
    order_id: str,
    qty: float | None = None,
    limit_price: float | None = None,
    stop_price: float | None = None,
) -> OrderResult:
    """Change qty/limit_price/stop_price on an open order. Not supported
    for notional orders or OTO legs — those can only be canceled."""
    if qty is not None and qty > MAX_ORDER_QTY:
        raise ModelRetry(f"{qty} shares exceeds the {MAX_ORDER_QTY}-share order limit.")
    try:
        order = ctx.deps.broker.replace_order(
            order_id, qty=qty, limit_price=limit_price, stop_price=stop_price
        )
    except Exception as e:
        raise ModelRetry(
            f"Couldn't replace order {order_id}: {e}. Notional orders and "
            "OTO legs can't be replaced, only canceled and re-placed."
        )
    return _to_result(order)


@execution_agent.tool
def get_trade_history(
    ctx: RunContext[ExecutionDeps],
    after: str | None = None,
    until: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Fetch fill-level trade activity (the audit trail), most recent
    first. Defaults to the last 10 fills — increase limit if the user
    asks for more, but keep it modest, large results can exceed what
    the model can process in one response."""
    return ctx.deps.broker.get_trade_activities(after=after, until=until, page_size=limit)


if __name__ == "__main__":
    # Interactive CLI for manually chatting with the agent.
    # Run with: uv run python -m compass.execution_agent
    from compass.tracing import enable_tracing, traced_run

    enable_tracing()

    deps = ExecutionDeps(broker=AlpacaBroker.from_env())
    # One trace for the whole CLI session (every turn until exit), not
    # per-turn — to_cli_sync runs its own internal loop we can't hook
    # into per-message.
    with traced_run("execution_agent_cli_session"):
        execution_agent.to_cli_sync(deps=deps, prog_name="compass-execution")
