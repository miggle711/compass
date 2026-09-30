"""Dataset-based eval for the execution agent, run through Langfuse.

Dataset is the 12 example queries validated against Alpaca's real API
in SCRUM-13, with the tool each one is expected to trigger as the
expected_output. Run with: uv run python -m compass.eval_execution_agent
"""

from __future__ import annotations

from langfuse import Evaluation, get_client
from langfuse.api.commons.errors.not_found_error import NotFoundError
from pydantic_ai.exceptions import ModelHTTPError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from compass.broker import AlpacaBroker
from compass.execution_agent import ExecutionDeps, execution_agent
from compass.tracing import enable_tracing, traced_run


def _is_rate_limit_error(exc: BaseException) -> bool:
    return isinstance(exc, ModelHTTPError) and exc.status_code == 429

DATASET_NAME = "execution-agent-query-families"

CASES: list[tuple[str, str]] = [
    ("Buy 10 shares of AAPL at market price.", "place_market_order"),
    ("What's the status of my last AAPL order?", "get_order_status"),
    ("Cancel my pending order for TSLA.", "cancel_order"),
    ("Did my order for MSFT actually fill, and at what price?", "get_trade_history"),
    ("Show me everything I've traded this week.", "get_trade_history"),
    ("Place a limit order to sell 5 shares of GOOG at $180.", "place_limit_order"),
    ("Give me a log of every order I've placed, cancelled, or that got rejected.", "get_trade_history"),
    (
        "Buy 1 share of GOOG with a take-profit above and a stop-loss below the current price.",
        "place_bracket_order",
    ),
    ("I want to spend $500 on TSLA, not a specific number of shares.", "place_market_order"),
]


def seed_dataset(langfuse) -> None:
    """Create the dataset if needed, and add any CASES not already in it.

    Matches on exact query text, so editing a query in CASES adds a new
    item rather than updating the old one — the SDK has no delete, so
    stale items need removing by hand in the Langfuse UI.
    """
    try:
        existing = {item.input["query"] for item in langfuse.get_dataset(DATASET_NAME).items}
    except NotFoundError:
        langfuse.create_dataset(name=DATASET_NAME)
        existing = set()

    for query, expected_tool in CASES:
        if query in existing:
            continue
        langfuse.create_dataset_item(
            dataset_name=DATASET_NAME,
            input={"query": query},
            expected_output={"tool": expected_tool},
        )


@retry(
    retry=retry_if_exception(_is_rate_limit_error),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
async def run_agent(*, item, **kwargs) -> str:
    """Task function: run one query through the agent, return the tool
    it called (or "none" if it answered without calling a tool).

    Retries on Groq free-tier 429s (tight TPM limits) with backoff;
    any other failure propagates immediately.
    """
    deps = ExecutionDeps(broker=AlpacaBroker.from_env())
    with traced_run("eval_tool_choice_run"):
        result = await execution_agent.run(item.input["query"], deps=deps)
    tool_calls = [
        part.tool_name
        for msg in result.all_messages()
        for part in getattr(msg, "parts", [])
        if type(part).__name__ == "ToolCallPart"
    ]
    return tool_calls[-1] if tool_calls else "none"


def tool_choice_correct(*, input, output, expected_output, **kwargs) -> Evaluation:
    """Did the agent call the tool SCRUM-13 says this query should trigger?"""
    expected = expected_output["tool"]
    correct = output == expected
    return Evaluation(
        name="tool_choice_correct",
        value=1.0 if correct else 0.0,
        comment=f"expected {expected}, got {output}",
    )


if __name__ == "__main__":
    enable_tracing()
    langfuse = get_client()

    seed_dataset(langfuse)
    dataset = langfuse.get_dataset(DATASET_NAME)
    result = dataset.run_experiment(
        name="execution-agent-tool-choice",
        task=run_agent,
        evaluators=[tool_choice_correct],
    )
    print(result.format(include_item_results=True))
