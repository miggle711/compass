# Evaluations

How the execution agent is tested for behavioral reliability, beyond
unit-level correctness. See [architecture.md](architecture.md) for
where this fits in the overall design.

Both evals run through Langfuse's dataset/experiment framework, against
the **real** execution agent and the **real** Alpaca paper account, no
mocking.

## Running them

```bash
uv run python -m compass.eval_execution_agent        # tool choice
uv run python -m compass.eval_execution_agent_args    # tool choice + argument correctness
```

Both print a summary to the terminal and a link to the full run in
Langfuse. Running them against Groq's free tier can hit rate limits
(8,000 tokens/minute), both scripts retry on `429`s with backoff, but
a `413` ("request too large") means the single request itself exceeded
the budget and won't succeed no matter how long you wait.

## `eval_execution_agent.py`: tool choice

Dataset: [`execution-agent-query-families`](https://jp.cloud.langfuse.com/project/cmudubt3x001uad0ca8ympjku/datasets/cmudvh4fr0049ad0ccns7n7d9) (Langfuse, requires project access).

Checks if the agent calls the *right tool* (or correctly
calls *no tools*) for a natural-language query? Doesn't check arguments.

Covers market/limit/bracket order placement, trade-history lookups, and
(since the SCRUM-14 fix) the cases where the agent should refuse to
infer an order and ask for clarification instead.

**One dataset item is flagged as known-nondeterministic** ("Did my
order for MSFT actually fill, and at what price?"),  across repeated
runs on 2026-10-07, the model flip-flopped between two defensible
outcomes (calling `get_trade_history`, or asking for an order ID). Both
are safe; neither is reliably what the model does.

## `eval_execution_agent_args.py` — tool choice + arguments

Dataset: [`execution-agent-tool-args`](https://jp.cloud.langfuse.com/project/cmudubt3x001uad0ca8ympjku/datasets/cmue6sim3000cad0c99020amp) (Langfuse, requires project access).

Checks both tool choice *and* whether the arguments passed match what
the query actually asked for,  e.g. "buy 10 shares of AAPL" should
produce `place_market_order(symbol="AAPL", side="buy", qty=10)`, not
just any call to that tool.

Two kinds of case:

- **Static**: fixed query, fixed expected args (placing orders,
  history lookups).
- **Order-dependent** (`cancel_order`, `get_order_status`,
  `replace_order`): the query has to reference a *real* order ID, so
  the task function places a real throwaway order first, builds the
  query around its real ID, then checks the agent extracted and passed
  that exact ID back. A `try/finally` cancels the throwaway order
  afterward regardless of outcome, so the paper account doesn't
  accumulate test clutter.

`replace_order` needed extra care: a successful replace returns a
**new** order (Alpaca marks the original `replaced`, it isn't mutated
in place), so cleanup tracks the new ID from the tool's return value,
not the original one.

## Current findings (as of 2026-10-07)

Measured, not assumed, and the reason the SCRUM-14 "require explicit
order ID" design exists:

| Scenario | Result |
|---|---|
| Explicit order ID given directly | ~100% reliable (15/15 across 3 runs, pre-fix verification) |
| Agent must infer which order from an ambiguous reference | ~67% reliable, high run-to-run variance (55.8%-77.8% across 3 runs) |
| Post-fix: agent asked to infer, should now refuse instead | Mostly reliable, with one known-nondeterministic case (see above) |

[Representative post-fix run](https://jp.cloud.langfuse.com/project/cmudubt3x001uad0ca8ympjku/datasets/cmudvh4fr0049ad0ccns7n7d9/runs/d58bf09c-635c-4f62-8d12-60fa33e87800) (2026-10-07, 9/9 items against the corrected dataset).


## What's not covered yet

- `get_last_price` has no dedicated eval coverage in either dataset.
- No adversarial/negative cases — nothing tests that the agent
  correctly *refuses* an oversized order (the SCRUM-17 guardrail) or
  handles a nonsensical symbol.
- No check on output *content* (e.g. does the response actually mention
  "paper account" as the system prompt requires) — only tool choice and
  arguments.

## Keeping the dataset in sync with the code

The dataset dedupes on exact query text, not on the full case. If you
change a query's *expected* answer in `CASES` without changing its
*text*, the old (now-wrong) item in Langfuse won't be touched by
re-running the eval — it has to be deleted directly:

```python
from langfuse import get_client
client = get_client()
ds = client.get_dataset("execution-agent-query-families")
for item in ds.items:
    if item.input["query"] == "the exact stale query text":
        client.api.dataset_items.delete(item.id)
```

