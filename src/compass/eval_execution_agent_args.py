"""Tool-argument eval for the execution agent, run through Langfuse.

Separate from eval_execution_agent.py (which only checks tool CHOICE):
this checks whether the arguments the agent passed to its chosen tool
actually match what the query asked for — e.g. "buy 10 shares of AAPL"
should produce place_market_order(symbol="AAPL", side="buy", qty=10),
not just "some call to place_market_order."

Two kinds of case:
- "static": a fixed query with fixed expected args (place/history/price
  lookups — nothing that depends on prior state).
- an order-dependent kind ("cancel_order", "get_order_status",
  "replace_order"): dataset item stores the case KIND, not literal
  text, because the query has to reference a real order ID that only
  exists once run_agent places a throwaway order for it. This is more
  expensive (an extra order per run) but tests the realistic case: did
  the agent extract and pass the ID of an order that actually exists,
  not just echo a string back.

Runs real tool calls against the live paper account. Run with:
uv run python -m compass.eval_execution_agent_args
"""

from __future__ import annotations

import json

from alpaca.trading.enums import OrderSide
from langfuse import Evaluation, get_client
from langfuse.api.commons.errors.not_found_error import NotFoundError
from pydantic_ai.exceptions import ModelHTTPError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from compass.broker import AlpacaBroker
from compass.execution_agent import ExecutionDeps, execution_agent
from compass.tracing import enable_tracing, traced_run

DATASET_NAME = "execution-agent-tool-args"


def _is_rate_limit_error(exc: BaseException) -> bool:
    return isinstance(exc, ModelHTTPError) and exc.status_code == 429


# Static cases: (case_type, query, expected tool, expected args).
# Only the args listed are checked — an agent that adds extra optional
# args (e.g. a default time_in_force) isn't penalized, only missing or
# wrong values for the args we do list are.
STATIC_CASES: list[tuple[str, str, dict]] = [
    (
        "Buy 10 shares of AAPL at market price.",
        "place_market_order",
        {"symbol": "AAPL", "side": "buy", "qty": 10},
    ),
    (
        "Place a limit order to sell 5 shares of GOOG at $180.",
        "place_limit_order",
        {"symbol": "GOOG", "side": "sell", "qty": 5, "limit_price": 180},
    ),
    (
        "I want to spend $500 on TSLA, not a specific number of shares.",
        "place_market_order",
        {"symbol": "TSLA", "side": "buy", "notional": 500},
    ),
]

# Order-dependent cases: (case_kind, expected tool). The query text and
# expected order_id are generated at run time in run_agent, since they
# depend on an order placed fresh for each run.
ORDER_DEPENDENT_CASES: list[tuple[str, str]] = [
    ("cancel_order", "cancel_order"),
    ("get_order_status", "get_order_status"),
]


def seed_dataset(langfuse) -> None:
    """Create the dataset if needed, and add any CASES not already in it."""
    try:
        existing_inputs = {
            (item.input.get("case_type"), item.input.get("query"))
            for item in langfuse.get_dataset(DATASET_NAME).items
        }
    except NotFoundError:
        langfuse.create_dataset(name=DATASET_NAME)
        existing_inputs = set()

    for query, expected_tool, expected_args in STATIC_CASES:
        key = ("static", query)
        if key in existing_inputs:
            continue
        langfuse.create_dataset_item(
            dataset_name=DATASET_NAME,
            input={"case_type": "static", "query": query},
            expected_output={"tool": expected_tool, "args": expected_args},
        )

    for case_kind, expected_tool in ORDER_DEPENDENT_CASES:
        key = (case_kind, None)
        if key in existing_inputs:
            continue
        langfuse.create_dataset_item(
            dataset_name=DATASET_NAME,
            input={"case_type": case_kind},
            expected_output={"tool": expected_tool},
        )


def _setup_order_for_case(broker: AlpacaBroker, case_type: str) -> tuple[str, str]:
    """Place a real throwaway order for an order-dependent case, and
    return (query text, real order id) built around it.

    Uses a cheap, far-from-market limit order so it stays open (not
    filled) for the duration of the run — matters for the cancel case,
    which needs the order to still be cancelable.
    """
    order = broker.place_limit_order("AAPL", OrderSide.BUY, qty=1, limit_price=1.00)
    order_id = str(order.id)

    if case_type == "cancel_order":
        return f"Cancel my order {order_id}.", order_id
    if case_type == "get_order_status":
        return f"What's the status of order {order_id}?", order_id
    raise ValueError(f"Unknown order-dependent case type: {case_type}")


@retry(
    retry=retry_if_exception(_is_rate_limit_error),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
async def run_agent(*, item, **kwargs) -> dict:
    """Task function: run one case, return the last tool call's name and args.

    For order-dependent cases, places a real order first so the query
    references a real ID, and stashes that ID on the result so
    args_correct can check the agent passed the same one back.
    """
    deps = ExecutionDeps(broker=AlpacaBroker.from_env())
    case_type = item.input["case_type"]

    expected_order_id = None
    if case_type == "static":
        query = item.input["query"]
    else:
        query, expected_order_id = _setup_order_for_case(deps.broker, case_type)

    try:
        with traced_run("eval_tool_args_run"):
            result = await execution_agent.run(query, deps=deps)
        tool_calls = [
            part
            for msg in result.all_messages()
            for part in getattr(msg, "parts", [])
            if type(part).__name__ == "ToolCallPart"
        ]
        if not tool_calls:
            return {"tool": "none", "args": {}, "expected_order_id": expected_order_id}
        last_call = tool_calls[-1]
        return {
            "tool": last_call.tool_name,
            "args": json.loads(last_call.args),
            "expected_order_id": expected_order_id,
        }
    finally:
        # Safety net: whatever the agent did (or didn't do), make sure
        # the throwaway setup order doesn't stay open on the paper
        # account. Already-terminal orders (e.g. the agent correctly
        # canceled it) reject a second cancel — that's expected, not
        # an eval failure, so it's swallowed here.
        if expected_order_id is not None:
            try:
                deps.broker.cancel_order(expected_order_id)
            except Exception:
                pass


def tool_choice_correct(*, input, output, expected_output, **kwargs) -> Evaluation:
    expected = expected_output["tool"]
    correct = output["tool"] == expected
    return Evaluation(
        name="tool_choice_correct",
        value=1.0 if correct else 0.0,
        comment=f"expected {expected}, got {output['tool']}",
    )


def args_correct(*, input, output, expected_output, **kwargs) -> Evaluation:
    """Every expected arg must be present in the actual call with the
    same value. Extra args the agent adds (e.g. defaults) don't count
    against it — only missing or mismatched expected args do.

    Order-dependent cases (cancel_order, get_order_status) have no
    static expected args in the dataset — the expected order_id is
    only known once run_agent places the real order, so it's compared
    from output["expected_order_id"] instead.
    """
    if output["tool"] != expected_output["tool"]:
        return Evaluation(name="args_correct", value=0.0, comment="wrong tool, args not checked")

    if output.get("expected_order_id") is not None:
        actual_id = output["args"].get("order_id")
        expected_id = output["expected_order_id"]
        if actual_id != expected_id:
            return Evaluation(
                name="args_correct", value=0.0,
                comment=f"expected order_id {expected_id}, got {actual_id}",
            )
        return Evaluation(name="args_correct", value=1.0, comment="order_id matched")

    expected_args = expected_output["args"]
    actual_args = output["args"]
    mismatches = {
        key: (expected_val, actual_args.get(key))
        for key, expected_val in expected_args.items()
        if actual_args.get(key) != expected_val
    }
    if mismatches:
        return Evaluation(name="args_correct", value=0.0, comment=f"mismatches: {mismatches}")
    return Evaluation(name="args_correct", value=1.0, comment="all expected args matched")


if __name__ == "__main__":
    enable_tracing()
    langfuse = get_client()

    seed_dataset(langfuse)
    dataset = langfuse.get_dataset(DATASET_NAME)
    result = dataset.run_experiment(
        name="execution-agent-tool-args",
        task=run_agent,
        evaluators=[tool_choice_correct, args_correct],
    )
    print(result.format(include_item_results=True))
