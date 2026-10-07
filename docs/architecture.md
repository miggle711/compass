# Architecture

How Compass's execution agent is actually built, as of this doc. For the
original pre-code planning notes (now mostly superseded), see
[architecture-notes.md](architecture-notes.md).

## Layers

```
api.py              FastAPI wrapper 
    |
execution_agent.py   Pydantic AI Agent 
    |
broker.py            AlpacaBroker 
    |
Alpaca's REST API
```

Each layer only talks to the one below it. `execution_agent.py` never
calls Alpaca directly and every tool goes through `AlpacaBroker`. This
allows  `broker.py` to get verified against the real paper
account independently, before any LLM was involved: bugs showed up as
either a broker bug (caught early, cheap) or a model/tool-choice bug
(the thing the evals exist to catch), never a tangled mix of both.

## AlpacaBroker (`broker.py`)

A thin wrapper over Alpaca's `TradingClient` and
`StockHistoricalDataClient`, plus one raw REST call
(`get_trade_activities`) for the Account Activities endpoint, which
`alpaca-py`'s SDK doesn't wrap.

Paper-vs-live is fixed at construction (`TradingClient(..., paper=True)`
hardcoded in `__init__`), not a parameter callers or the model can
influence. This is a structural guard-rail to preven

## The agent (`execution_agent.py`)

A single Pydantic AI `Agent`, currently on Groq's free tier
(`openai/gpt-oss-120b`, via its OpenAI-compatible endpoint rather than
Pydantic AI's `"groq:"` shorthand, so swapping providers later is a
`base_url`/model-name change only).

Each tool (`place_market_order`, `get_order_status`, `cancel_order`,
etc.) is a 2-3 line translator: typed args in, a call to
`ctx.deps.broker.<method>()`, a typed `OrderResult` out. No
Alpaca-specific logic lives in this file.

Two guardrails are enforced here, not in the broker, because they're
Compass policy rather than Alpaca API constraints:

- **Order size limits** (`MAX_ORDER_QTY` = 100 shares,
  `MAX_ORDER_NOTIONAL` = $10,000) — checked before every `place_*` call,
  converting qty-based orders to an implied notional via
  `get_last_price` so a small share count in an expensive stock can't
  bypass the dollar cap.
- **Explicit order IDs required** for `get_order_status`, `cancel_order`,
  `replace_order` — the agent will not infer which order a user means
  from context; it asks for the ID or offers to list trade history
  instead. See [Behavior](#behavior-asking-vs-guessing) below for why.

Violations of either guardrail raise an internal exception that gets
turned into a `ModelRetry`, so the agent explains the limit to the user
instead of the run crashing.

## The API (`api.py`)

A FastAPI app with one real route, `POST /chat`. Stateless across
requests — each call builds a fresh `AlpacaBroker`/`ExecutionDeps`, and
multi-turn conversation works by the caller round-tripping message
history (serialized via `ModelMessagesTypeAdapter`), not server-side
sessions.

Auth is a static API key (`X-API-Key` header, checked against
`COMPASS_API_KEY`), this endpoint can place real orders, so it's never
exposed without it.

Client-supplied history is treated as a trust boundary: inbound history
is validated then run through Pydantic AI's `sanitize_messages()`
before being handed to the agent, per Pydantic AI's own guidance on
untrusted history (a client could otherwise fabricate prior tool
calls).

See [api-usage.md](api-usage.md) for how to actually call it.

## Behavior: asking vs. guessing

The system prompt draws a hard line: `get_order_status`, `cancel_order`,
and `replace_order` only get called if the user's message already
contains the order ID. If someone says "cancel my pending TSLA order"
with no ID, the agent does not try to resolve which order that is. It
asks, or offers to pull `get_trade_history` and let the user pick.

This exists because of a measured, not assumed, reliability gap (see
[evals.md](evals.md)): the free-tier model was reliable (~100%, 15/15
runs) when given an explicit order ID, but unreliable (~67%, high
variance) when it had to infer the right order from an ambiguous
reference first.

`get_trade_history` itself has no such restriction; it's read-only and
returns a list, so there's no risk of acting on the wrong order from
it. In practice the model doesn't always respect that distinction
cleanly, see the "known nondeterministic" case documented in
`eval_execution_agent.py`.

## Tracing (`tracing.py`)

Pydantic AI has no dedicated Langfuse integration, Langfuse consumes
the OpenTelemetry spans Pydantic AI emits once instrumented
(`Agent.instrument_all()`).

Without an active root span, each model call
and tool call has no parent to nest under, and fragments into its own
separate trace instead of one coherent tree. `traced_run(name)` wraps a
call in `tracer.start_as_current_span(name)` to fix this, every
`execution_agent.run(...)` call in this codebase is wrapped in it.

## Evaluation

Two Langfuse dataset evals, both running real tool calls against the
real paper account (no mocking):

- `eval_execution_agent.py`:  tool **choice** only, over ambiguous/ID-less
  queries.
- `eval_execution_agent_args.py`:tool choice **and argument
  correctness**, including order-dependent cases that place a real
  throwaway order so the query references a real ID.

See [evals.md](evals.md) for what's covered, current findings, and how
to run them.

## Deployment

Cloud Run, containerized via the repo's `Dockerfile`, auto-deployed on
push to `main` via a Cloud Build trigger, gated by GitHub Actions CI
(branch protection on `main` requires the `ci` check to pass before
merge). Secrets live in Google Secret Manager, not baked into the image
or committed to the repo.

See [deployment.md](deployment.md) for the full setup and how to
reproduce or modify it.

## What's deliberately not here yet

- **No orchestration layer.** This is one agent, not a multi-agent
  graph. `pydantic-graph`/LangGraph only becomes relevant once a
  coordinator needs to route between this agent and others (price,
  news, filings) that teammates are building, see
  [architecture-notes.md](architecture-notes.md) for the original
  thinking on this.
- **No automated test suite.** Everything so far has been verified
  manually or via the Langfuse evals, never with pytest (tracked in
  Jira as SCRUM-20).
- **No rate limiting of our own** on the API, beyond what Groq itself
  enforces (tracked as SCRUM-23).
