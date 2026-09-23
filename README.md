# compass

An agentic assistant for market research which aggregates live prices, news, and filings across multiple providers, and explains what it finds without telling you what to trade.

## Status

Early stage. Provider research (price data, news/sentiment, trade execution) is tracked in Jira under `SCRUM-6`. The trade execution subagent is the first one built out, see `src/compass/`.

## Setup

```bash
uv sync
cp .env.example .env  # fill in real values
```

Required in `.env`:

- `GROQ_API_KEY`: free tier, used via Groq's OpenAI-compatible endpoint
- `ALPACA_PAPER_KEY_ID` / `ALPACA_PAPER_SECRET_KEY`: Alpaca paper trading account
- `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL`: free tier tracing

## Execution agent

Places and manages orders on the user's Alpaca paper account. Tools are thin wrappers over `AlpacaBroker` (`src/compass/broker.py`), verified independently against the real Alpaca API.

```bash
uv run python -m compass.execution_agent   # interactive CLI
```

Paper-vs-live is enforced in `AlpacaBroker` itself, not exposed to the model as something it could influence.

### Evals

```bash
uv run python -m compass.eval_execution_agent        # tool choice
uv run python -m compass.eval_execution_agent_args    # tool choice + argument correctness
```

Both run against Langfuse datasets and the real paper account. Current finding (tracked in `SCRUM-14`): the free-tier model is reliable when given an explicit order ID, but unreliable (~67%, high variance) when it has to infer which order the user means without one.

## Project layout

```text
src/compass/
  broker.py                      AlpacaBroker, Alpaca Trading + Market Data API wrapper
  models.py                      Shared typed outputs (OrderResult)
  execution_agent.py             Trade execution Pydantic AI agent
  tracing.py                     Langfuse/OTel tracing setup
  eval_execution_agent.py        Tool-choice eval
  eval_execution_agent_args.py   Tool-argument eval
docs/
  architecture-notes.md          Early exploration: LangGraph + Pydantic AI
```

## Next steps

- Write a fuller system doc
- Deploy the execution agent so it's callable remotely (Cloud Run?)
